import pytest

from guiexp_android.recovery_validation_v4 import literal_goal_parameters


def test_note_name_without_extension_is_not_changed():
    assert literal_goal_parameters('MarkorDeleteNote','Delete the note in Markor named copy_glad_vase.') == {'file_name':'copy_glad_vase'}


def test_actual_extension_is_preserved():
    assert literal_goal_parameters('MarkorDeleteNote','Delete the note in Markor named notes.txt.md.') == {'file_name':'notes.txt.md'}


def test_move_fields_are_literal_and_storage_must_match():
    goal='Move the file my_file.mp3 from DCIM within the sdk_gphone64_arm64 storage area to the Recordings within the same sdk_gphone64_arm64 storage area in the Android filesystem.'
    assert literal_goal_parameters('FilesMoveFile',goal)=={'file_name':'my_file.mp3','source_folder':'DCIM','destination_folder':'Recordings'}
    with pytest.raises(ValueError):
        literal_goal_parameters('FilesMoveFile',goal.replace('same sdk_gphone64_arm64','same wrong_storage'))


def test_unrecognized_instruction_is_not_guessed():
    with pytest.raises(ValueError):
        literal_goal_parameters('MarkorDeleteNote','Delete whichever note looks oldest.')
