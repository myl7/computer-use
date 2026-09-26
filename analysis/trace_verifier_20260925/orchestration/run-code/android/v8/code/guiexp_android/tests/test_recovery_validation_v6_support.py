"""Separate-process regression for the actual AndroidWorld noise generator."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = r'''
import contextlib, json, random
from android_world.task_evals.utils import user_data_generation as u
from guiexp_android.recovery_validation_v6_support import canonical_noise_setup
from android_world.utils import file_utils
import sys
rows = {}
# Keep real create_file random-content generation, mock only device writes.
file_utils.mkdir = lambda *args: None
file_utils.adb_utils.issue_generic_request = lambda args, env: rows.update({args[-1]: args[2]})
original = u.generate_noise_files
manager = canonical_noise_setup(u) if sys.argv[1] == 'canonical' else contextlib.nullcontext()
random.seed(917210)
with manager:
    u.generate_noise_files('target.txt', '/public', None, ['target.txt', 'draft.md'], 20)
assert u.generate_noise_files is original
print(json.dumps(rows, sort_keys=True))
'''


def run(mode, hash_seed):
    env = dict(os.environ, PYTHONHASHSEED=str(hash_seed),
               PYTHONPATH=os.pathsep.join([str(ROOT / 'computer-use'), str(ROOT / 'third-party/android_world')]))
    return json.loads(subprocess.check_output([sys.executable, '-c', SCRIPT, mode], env=env, text=True))


def test_canonical_contents_match_across_separate_processes():
    legacy = [run('legacy', seed) for seed in (1, 2, 3)]
    fixed = [run('canonical', seed) for seed in (1, 2, 3)]
    assert legacy[0] != legacy[1]  # This fixture reproduces the original failure.
    assert fixed[0] == fixed[1] == fixed[2]
    for old, new in zip(legacy, fixed):
        assert old.keys() == new.keys()
        assert sorted(old.values()) == sorted(new.values())


def test_restores_original_after_exception_and_rejects_nested_setup():
    sys.path.insert(0, str(ROOT / 'third-party/android_world'))
    from android_world.task_evals.utils import user_data_generation as u
    from guiexp_android.recovery_validation_v6_support import canonical_noise_setup
    import pytest
    original = u.generate_noise_files
    with pytest.raises(ValueError):
        with canonical_noise_setup(u):
            with pytest.raises(RuntimeError, match='changed or canonical'):
                with canonical_noise_setup(u):
                    pass
            raise ValueError('initializer failed')
    assert u.generate_noise_files is original
