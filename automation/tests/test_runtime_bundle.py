import hashlib
import json
import os

import pytest

from iot_exp.runtime_bundle import (
    canonical_path,
    filesystem_path,
    load_manifest,
    prepare_appium_home,
    private_environment,
)


def bundle(tmp_path, install_path='node_modules/driver'):
    root = tmp_path / 'resources'
    target = 'windows-x64' if os.name == 'nt' else 'linux-x64'
    executables = {name: f'runtime/{target}/{name}/entry' for name in ('python','node','java','appium')}
    for relative in executables.values():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('pinned payload')
    appium = root / f'runtime/{target}/appium'
    (appium / 'node_modules/driver').mkdir(parents=True)
    (appium / 'node_modules/driver/package.json').write_text(json.dumps({'version':'6.9.3'}))
    (appium / 'extension-template.json').write_text(json.dumps({'schemaRev':4,'drivers':{'uiautomator2':{'version':'6.9.3','installPath':install_path}}}))
    manifest = {'schema':1,'protocol_version':1,'target':target,'bundle_id':'test-bundle',
                'executables':executables,'files':{path.relative_to(root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob('*') if path.is_file()}}
    (root / 'runtime-manifest.json').write_text(json.dumps(manifest))
    return root, manifest


def test_corrupt_core_is_diagnosed_but_optional_damage_does_not_block_core(tmp_path):
    root, manifest = bundle(tmp_path)
    (root / manifest['executables']['node']).unlink()
    assert load_manifest(root)['bundle_id'] == 'test-bundle'
    environment = private_environment(root, tmp_path / 'state', manifest)
    assert environment['IOT_EXP_NODE_EXECUTABLE'].endswith('unverified-node')
    (root / manifest['executables']['python']).write_text('corrupted')
    with pytest.raises(ValueError, match='校验失败'):
        load_manifest(root)


def test_appium_index_relocates_and_payload_damage_never_gets_installed(tmp_path):
    root, manifest = bundle(tmp_path)
    state = tmp_path / 'state'
    home = prepare_appium_home(root, state, manifest)
    index = (home / 'node_modules/.cache/appium/extensions.yaml').read_text()
    assert str(home / 'node_modules/driver').replace('\\', '/') in index.replace('\\', '/')
    (home / 'node_modules/driver/package.json').write_text('corrupted')
    with pytest.raises(ValueError, match='载荷损坏'):
        prepare_appium_home(root, state, manifest)


def test_driver_index_cannot_escape_private_home(tmp_path):
    root, manifest = bundle(tmp_path, '../outside')
    with pytest.raises(ValueError, match='越界'):
        prepare_appium_home(root, tmp_path / 'state', manifest)


def test_old_appium_status_cannot_enable_a_new_bundle(tmp_path):
    root, manifest = bundle(tmp_path)
    state = tmp_path / 'state'
    prepare_appium_home(root, state, manifest)
    (state / 'runtime-status.json').write_text(json.dumps({'prepared':True,'bundle_id':'old-bundle'}))
    assert private_environment(root, state, manifest)['IOT_EXP_APPIUM_ENTRY'].endswith('not-ready')


@pytest.mark.skipif(os.name != 'nt', reason='Windows MAX_PATH regression')
def test_appium_deep_payload_initializes_in_long_install_and_user_paths(tmp_path):
    long_base = tmp_path / ('安装目录' * 15) / ('用户状态' * 15)
    filesystem_path(long_base).mkdir(parents=True)
    root, manifest = bundle(filesystem_path(long_base))
    root = canonical_path(root)
    relative = 'node_modules/driver/node_modules/' + '/'.join(['nested-dependency-' * 3] * 4) + '/index.js'
    source = root / f'runtime/{manifest["target"]}/appium' / relative
    assert len(str(source)) > 260
    filesystem_path(source.parent).mkdir(parents=True)
    filesystem_path(source).write_text('exact immutable deep payload', encoding='utf-8')
    manifest['files'][source.relative_to(root).as_posix()] = hashlib.sha256(b'exact immutable deep payload').hexdigest()
    state = canonical_path(long_base / 'state')
    home = prepare_appium_home(root, state, manifest)
    assert filesystem_path(home / relative).read_text('utf-8') == 'exact immutable deep payload'
    assert filesystem_path(home / 'bundle.json').is_file()
    assert not str(home).startswith('\\\\?\\')
    assert prepare_appium_home(root, state, manifest) == home
    filesystem_path(home / relative).write_text('corrupted', encoding='utf-8')
    with pytest.raises(ValueError, match='载荷损坏'):
        prepare_appium_home(root, state, manifest)
