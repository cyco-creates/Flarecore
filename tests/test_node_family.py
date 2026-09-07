from test_nodes import PKG


def test_flarecore_family_preserves_node_ids():
    expected = {'FlareRender', 'FlarePresetLoader', 'FlareDepthAdapter',
                'FlareKeyframes', 'FlareElementPrompts', 'FlareTexturePrepare',
                'FlareElementSave', 'FlareGeneratorSelect'}
    assert set(PKG.NODE_CLASS_MAPPINGS) == expected
    assert set(PKG.NODE_DISPLAY_NAME_MAPPINGS) == expected
    for key, cls in PKG.NODE_CLASS_MAPPINGS.items():
        assert cls.CATEGORY == 'Flarecore'
        assert PKG.NODE_DISPLAY_NAME_MAPPINGS[key].startswith('Flarecore · ')
