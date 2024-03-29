import logging
import os
import subprocess

from typing import List, Optional, Union

from securedrop_export.exceptions import ExportException

from .volume import EncryptionScheme, Volume, MountedVolume
from .status import Status

logger = logging.getLogger(__name__)


class CLI:
    """
    A Python wrapper for various shell commands required to detect, map, and
    mount Export devices.

    CLI callers must handle ExportException and all exceptions and exit with
    sys.exit(0) so that another program does not attempt to open the submission.
    """

    # Default mountpoint (unless drive is already mounted manually by the user)
    _DEFAULT_MOUNTPOINT = "/media/usb"

    def get_connected_devices(self) -> List[str]:
        """
        List all block devices attached to VM that are disks and not partitions.
        Return list of all removable connected block devices.

        Raise ExportException if any commands fail.
        """
        logger.info("Checking connected volumes")
        try:
            lsblk = subprocess.Popen(
                ["lsblk", "-o", "NAME,TYPE"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            grep = subprocess.Popen(
                ["grep", "disk"],
                stdin=lsblk.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            command_output = grep.stdout.readlines()  # type: ignore[union-attr]

            # The first word in each element of the command_output list is the device name
            attached_devices = [x.decode("utf8").split()[0] for x in command_output]

        except subprocess.CalledProcessError as ex:
            raise ExportException(sdstatus=Status.DEVICE_ERROR) from ex

        return self._get_removable_devices(attached_devices)

    def _get_removable_devices(self, attached_devices: List[str]) -> List[str]:
        """
        Determine which block devices are USBs by selecting those that are removable.
        """
        logger.info("Checking removable devices")
        usb_devices = []
        for device in attached_devices:
            is_removable = False
            try:
                removable = subprocess.check_output(
                    ["cat", f"/sys/class/block/{device}/removable"],
                    stderr=subprocess.PIPE,
                )

                # removable is "0" for non-removable device, "1" for removable,
                # convert that into a Python boolean
                is_removable = bool(int(removable.decode("utf8").strip()))

            except subprocess.CalledProcessError:
                # Not a removable device
                continue

            if is_removable:
                usb_devices.append(f"/dev/{device}")

        logger.info(f"{len(usb_devices)} connected")
        return usb_devices

    def get_partitioned_device(self, blkid: str) -> str:
        """
        Given a string representing a block device, return string that includes correct partition
        (such as "/dev/sda" or "/dev/sda1").

        Raise ExportException if partition check fails or device has unsupported partition scheme
        (currently, multiple partitions are unsupported).
        """
        device_and_partitions = self._check_partitions(blkid)

        if device_and_partitions:
            partition_count = (
                device_and_partitions.decode("utf-8").split("\n").count("part")
            )
            logger.debug(f"Counted {partition_count} partitions")
            if partition_count > 1:
                # We don't currently support devices with multiple partitions
                logger.error(
                    f"Multiple partitions not supported ({partition_count} partitions"
                    f" on {blkid})"
                )
                raise ExportException(sdstatus=Status.INVALID_DEVICE_DETECTED)

            # redefine device to /dev/sda if disk is encrypted, /dev/sda1 if partition encrypted
            if partition_count == 1:
                logger.debug("One partition found")
                blkid += "1"

            return blkid

        else:
            # lsblk did not return output we could process
            logger.error("Error checking device partitions")
            raise ExportException(sdstatus=Status.DEVICE_ERROR)

    def _check_partitions(self, blkid: str) -> bytes:
        try:
            logger.debug(f"Checking device partitions on {blkid}")
            device_and_partitions = subprocess.check_output(
                ["lsblk", "-o", "TYPE", "--noheadings", blkid], stderr=subprocess.PIPE
            )
            return device_and_partitions

        except subprocess.CalledProcessError as ex:
            logger.error(f"Error checking block device {blkid}")
            raise ExportException(sdstatus=Status.DEVICE_ERROR) from ex

    def is_luks_volume(self, device: str) -> bool:
        """
        Given a string representing a volume (/dev/sdX or /dev/sdX1), return True if volume is
        LUKS-encrypted, otherwise False.
        """
        isLuks = False

        try:
            logger.debug("Checking if target device is luks encrypted")

            # cryptsetup isLuks returns 0 if the device is a luks volume
            # subprocess will throw if the device is not luks (rc !=0)
            subprocess.check_call(["sudo", "cryptsetup", "isLuks", device])

            isLuks = True

        except subprocess.CalledProcessError:
            # Not necessarily an error state, just means the volume is not LUKS encrypted
            logger.info("Target device is not LUKS-encrypted")

        return isLuks

    def _get_luks_name_from_headers(self, device: str) -> str:
        """
        Dump LUKS header and determine name of volume.

        Raise ExportException if errors encounterd during attempt to parse LUKS headers.
        """
        logger.debug("Get LUKS name from headers")
        try:
            luks_header = subprocess.check_output(
                ["sudo", "cryptsetup", "luksDump", device]
            )
            if luks_header:
                luks_header_list = luks_header.decode("utf-8").split("\n")
                for line in luks_header_list:
                    items = line.split("\t")
                    if "UUID" in items[0]:
                        return "luks-" + items[1]

            # If no header or no UUID field, we can't use this drive
            logger.error(
                f"Failed to get UUID from LUKS header; {device} may not be correctly formatted"
            )
            raise ExportException(sdstatus=Status.INVALID_DEVICE_DETECTED)
        except subprocess.CalledProcessError as ex:
            logger.error("Failed to dump LUKS header")
            raise ExportException(sdstatus=Status.DEVICE_ERROR) from ex

    def get_luks_volume(self, device: str) -> Union[Volume, MountedVolume]:
        """
        Given a string corresponding to a LUKS-partitioned volume, return a corresponding Volume
        object.

        If LUKS volume is already mounted, existing mountpoint will be preserved and a
        MountedVolume object will be returned.
        If LUKS volume is unlocked but not mounted, volume will be mounted at _DEFAULT_MOUNTPOINT,
        and a MountedVolume object will be returned.

        If device is still locked, mountpoint will not be set, and a Volume object will be retuned.
        Once the decrpytion passphrase is available, call unlock_luks_volume(), passing the Volume
        object and passphrase to unlock the volume.

        Raise ExportException if errors are encountered.
        """
        try:
            mapped_name = self._get_luks_name_from_headers(device)
            logger.debug(f"Mapped name is {mapped_name}")

            # Setting the mapped_name does not mean the device has already been unlocked.
            luks_volume = Volume(
                device_name=device,
                mapped_name=mapped_name,
                encryption=EncryptionScheme.LUKS,
            )

            # If the device has been unlocked, we can see if it's mounted and
            # use the existing mountpoint, or mount it ourselves.
            # Either way, return a MountedVolume.
            if os.path.exists(os.path.join("/dev/mapper/", mapped_name)):
                return self.mount_volume(luks_volume)

            # It's still locked
            else:
                return luks_volume

        except ExportException:
            logger.error("Failed to return luks volume")
            raise

    def unlock_luks_volume(self, volume: Volume, decryption_key: str) -> Volume:
        """
        Unlock a LUKS-encrypted volume.

        Raise ExportException if errors are encountered during device unlocking.
        """
        if volume.encryption is not EncryptionScheme.LUKS:
            logger.error("Must call unlock_luks_volume() on LUKS-encrypted device")
            raise ExportException(sdstatus=Status.DEVICE_ERROR)

        try:
            logger.debug("Unlocking luks volume {}".format(volume.device_name))
            p = subprocess.Popen(
                [
                    "sudo",
                    "cryptsetup",
                    "luksOpen",
                    volume.device_name,
                    volume.mapped_name,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            logger.debug("Passing key")
            p.communicate(input=str.encode(decryption_key, "utf-8"))
            rc = p.returncode

            if rc == 0:
                return Volume(
                    device_name=volume.device_name,
                    mapped_name=volume.mapped_name,
                    encryption=EncryptionScheme.LUKS,
                )
            else:
                logger.error("Bad volume passphrase")
                raise ExportException(sdstatus=Status.ERROR_UNLOCK_LUKS)

        except subprocess.CalledProcessError as ex:
            raise ExportException(sdstatus=Status.DEVICE_ERROR) from ex

    def _get_mountpoint(self, volume: Volume) -> Optional[str]:
        """
        Check for existing mountpoint.
        Raise ExportException if errors encountered during command.
        """
        logger.debug("Checking mountpoint")
        try:
            output = subprocess.check_output(
                ["lsblk", "-o", "MOUNTPOINT", "--noheadings", volume.device_name]
            )
            return output.decode("utf-8").strip()

        except subprocess.CalledProcessError as ex:
            logger.error(ex)
            raise ExportException(sdstatus=Status.ERROR_MOUNT) from ex

    def mount_volume(self, volume: Volume) -> MountedVolume:
        """
        Given an unlocked LUKS volume, return MountedVolume object.

        If volume is already mounted, mountpoint is not changed. Otherwise,
        volume is mounted at _DEFAULT_MOUNTPOINT.

        Raise ExportException if errors are encountered during mounting.
        """
        if not volume.unlocked:
            logger.error("Volume is not unlocked.")
            raise ExportException(sdstatus=Status.ERROR_MOUNT)

        mountpoint = self._get_mountpoint(volume)

        if mountpoint:
            logger.info("The device is already mounted--use existing mountpoint")
            return MountedVolume.from_volume(volume, mountpoint)

        else:
            logger.info("Mount volume at default mountpoint")
            return self._mount_at_mountpoint(volume, self._DEFAULT_MOUNTPOINT)

    def _mount_at_mountpoint(self, volume: Volume, mountpoint: str) -> MountedVolume:
        """
        Mount a volume at the supplied mountpoint, creating the mountpoint directory and
        adjusting permissions (user:user) if need be. `mountpoint` must be a full path.

        Return MountedVolume object.
        Raise ExportException if unable to mount volume at target mountpoint.
        """
        if not os.path.exists(mountpoint):
            try:
                subprocess.check_call(["sudo", "mkdir", mountpoint])
            except subprocess.CalledProcessError as ex:
                logger.error(ex)
                raise ExportException(sdstatus=Status.ERROR_MOUNT) from ex

        # Mount device /dev/mapper/{mapped_name} at /media/usb/
        mapped_device_path = os.path.join(
            volume.MAPPED_VOLUME_PREFIX, volume.mapped_name
        )

        try:
            logger.info(f"Mounting volume at {mountpoint}")
            subprocess.check_call(["sudo", "mount", mapped_device_path, mountpoint])
            subprocess.check_call(["sudo", "chown", "-R", "user:user", mountpoint])

        except subprocess.CalledProcessError as ex:
            logger.error(ex)
            raise ExportException(sdstatus=Status.ERROR_MOUNT) from ex

        return MountedVolume.from_volume(volume, mountpoint)

    def write_data_to_device(
        self,
        submission_tmpdir: str,
        submission_target_dirname: str,
        device: MountedVolume,
    ):
        """
        Move files to drive (overwrites files with same filename) and unmount drive.
        Drive is unmounted and files are cleaned up as part of the `finally` block to ensure
        that cleanup happens even if export fails or only partially succeeds.
        """

        try:
            target_path = os.path.join(device.mountpoint, submission_target_dirname)
            subprocess.check_call(["mkdir", target_path])

            export_data = os.path.join(submission_tmpdir, "export_data/")
            logger.debug("Copying file to {}".format(submission_target_dirname))

            subprocess.check_call(["cp", "-r", export_data, target_path])
            logger.info(
                "File copied successfully to {}".format(submission_target_dirname)
            )

        except (subprocess.CalledProcessError, OSError) as ex:
            logger.error(ex)
            raise ExportException(sdstatus=Status.ERROR_EXPORT) from ex

        finally:
            self.cleanup_drive_and_tmpdir(device, submission_tmpdir)

    def cleanup_drive_and_tmpdir(self, volume: MountedVolume, submission_tmpdir: str):
        """
        Post-export cleanup method. Unmount and lock drive and remove temporary
        directory. Currently called at end of `write_data_to_device()` to ensure
        device is always locked after export.

        Raise ExportException if errors during cleanup are encountered.
        """
        logger.debug("Syncing filesystems")
        try:
            subprocess.check_call(["sync"])
            umounted = self._unmount_volume(volume)
            if umounted:
                self._close_luks_volume(umounted)
            self._remove_temp_directory(submission_tmpdir)

        except subprocess.CalledProcessError as ex:
            logger.error("Error syncing filesystem")
            raise ExportException(sdstatus=Status.ERROR_EXPORT_CLEANUP) from ex

    def _unmount_volume(self, volume: MountedVolume) -> Volume:
        """
        Helper. Unmount volume
        """
        if os.path.exists(volume.mountpoint):
            logger.debug(f"Unmounting drive from {volume.mountpoint}")
            try:
                subprocess.check_call(["sudo", "umount", volume.mountpoint])

            except subprocess.CalledProcessError as ex:
                logger.error("Error unmounting device")
                raise ExportException(sdstatus=Status.DEVICE_ERROR) from ex
        else:
            logger.info("Mountpoint does not exist; volume was already unmounted")

        return Volume(
            device_name=volume.device_name,
            mapped_name=volume.mapped_name,
            encryption=volume.encryption,
        )

    def _close_luks_volume(self, unlocked_device: Volume) -> None:
        """
        Helper. Close LUKS volume
        """
        if os.path.exists(os.path.join("/dev/mapper", unlocked_device.mapped_name)):
            logger.debug("Locking luks volume {}".format(unlocked_device))
            try:
                subprocess.check_call(
                    ["sudo", "cryptsetup", "luksClose", unlocked_device.mapped_name]
                )

            except subprocess.CalledProcessError as ex:
                logger.error("Error closing device")
                raise ExportException(sdstatus=Status.DEVICE_ERROR) from ex

    def _remove_temp_directory(self, tmpdir: str):
        """
        Helper. Remove temporary directory used during archive export.
        """
        logger.debug(f"Deleting temporary directory {tmpdir}")
        try:
            subprocess.check_call(["rm", "-rf", tmpdir])
        except subprocess.CalledProcessError as ex:
            logger.error("Error removing temporary directory")
            raise ExportException(sdstatus=Status.DEVICE_ERROR) from ex
