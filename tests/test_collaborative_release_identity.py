"""Integrated workbench fixes need a new immutable update, not an r5 replacement."""
import update_client


def test_collaborative_workbench_build_is_newer_than_published_r5():
    assert update_client.build_key(update_client.CURRENT_TAG) > update_client.build_key(
        'v1.0.0.20261103.5'
    )
