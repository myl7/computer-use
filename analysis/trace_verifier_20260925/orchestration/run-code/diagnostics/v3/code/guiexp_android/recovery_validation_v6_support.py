"""Offline preflight support. No device, model, or budget access on import."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import inspect
import os

NOISE_SOURCE_SHA256 = "0bfd7814dc18b1370bfb1379c6972c15cd482a17160ca0859ad94cc881fd93cb"


@contextmanager
def canonical_noise_setup(module=None):
    """Canonicalize noise file creation only while a task initializer runs.

    The caller must exclusively own task initialization in this process. Set
    task.params['seed'] before env.reset(task), and wrap that reset here. This
    fixes name-to-content assignment, not app snapshots or filesystem metadata.
    A separate-process full public-state fingerprint remains mandatory.
    """
    if module is None:
        from android_world.task_evals.utils import user_data_generation as module
    original = module.generate_noise_files
    digest = hashlib.sha256(inspect.getsource(original).encode()).hexdigest()
    if digest != NOISE_SOURCE_SHA256:
        raise RuntimeError("Noise generator changed or canonical setup is already active")

    def generate_noise_files(base_file_name, directory_path, env, variant_names, n=20):
        # Same random draws and naming rules as the pinned upstream function.
        assert variant_names
        count = module.random.randint(1, n)
        names = set()
        while len(names) < count:
            if module.random.random() <= 0.85:
                selected = module.random.choice(variant_names)
                filename = module.generate_modified_file_name(selected)
            else:
                filename = module.generate_modified_file_name(base_file_name)
            if len(filename.split(".")) == 1:
                _, extension = os.path.splitext(module.random.choice(variant_names))
                filename += extension
            names.add(filename)
        for filename in sorted(names):
            module.file_utils.create_file(filename, directory_path, env)

    module.generate_noise_files = generate_noise_files
    try:
        yield {"upstream_noise_sha256": digest, "noise_order": "filename_unicode_ascending"}
    finally:
        module.generate_noise_files = original
