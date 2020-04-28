"""
Functional tests for sending messages in the SecureDrop client application. The
tests are based upon the client testing descriptions here:

https://github.com/freedomofpress/securedrop-client/wiki/Test-plan#basic-client-testing
"""
import pytest
from flaky import flaky
from PyQt5.QtCore import Qt

from tests.conftest import (TIME_APP_START, TIME_CLICK_ACTION,
                            TIME_RENDER_SOURCE_LIST)


@flaky
@pytest.mark.vcr()
def test_unstar_source(functional_test_logged_in_context, qtbot, mocker):
    """
    It's possible to un-star a source and see its updated status.
    """
    gui, controller, tempdir = functional_test_logged_in_context
    qtbot.wait(TIME_APP_START)

    def check_for_sources():
        assert len(list(gui.main_view.source_list.source_widgets.keys()))

    qtbot.waitUntil(check_for_sources, timeout=TIME_RENDER_SOURCE_LIST)
    source_ids = list(gui.main_view.source_list.source_widgets.keys())
    first_source_id = source_ids[0]
    first_source_widget = gui.main_view.source_list.source_widgets[first_source_id]
    qtbot.mouseClick(first_source_widget, Qt.LeftButton)

    # Check the source IS checked.
    assert first_source_widget.star.isChecked() is True
    # Click it again to toggle it off.
    qtbot.mouseClick(first_source_widget.star, Qt.LeftButton)
    qtbot.wait(TIME_CLICK_ACTION)
    # Check the source isn't checked once more.
    assert first_source_widget.star.is_starred is False
