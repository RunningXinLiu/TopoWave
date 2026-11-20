"""Locate portable, content-addressed desktop result bundles without using SSH."""
from pathlib import Path
import re

from custom_scene import state_root
from model import ROOT


def local_result(checksum):
    if not re.fullmatch('[a-f0-9]{64}', checksum or ''):
        return None
    for folder in (ROOT/'examples'/'offline_results', state_root()/'template_results'):
        path = folder/(checksum+'.npz')
        if path.is_file():
            return path
    return None


def result_for_ref(ref):
    previous = Path(ref.get('local_path') or '')
    if previous.is_file():
        return previous
    return local_result(ref.get('result_sha256'))
