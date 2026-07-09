from pathlib import Path

from deploy.collector_common import normalize_share_symbol
from deploy.deploy_vps import build_cron_block, has_vps_auth, load_fund_symbols, replace_managed_cron


def test_load_fund_symbols_formats_shanghai_and_shenzhen(tmp_path):
    config = tmp_path / "lof_config.yaml"
    config.write_text(
        """
funds:
  - code: "162411"
  - code: 501018
  - code: "159518"
  - code: ""
  - name: missing code
""",
        encoding="utf-8",
    )

    assert load_fund_symbols(config) == ["162411", "sh501018", "159518"]


def test_replace_managed_cron_is_idempotent():
    block = build_cron_block(
        "/home/ubuntu/LOFarb",
        "/home/ubuntu/LOFarb/siphon_data",
        "/home/ubuntu/LOFarb/.venv/bin/python",
    )
    existing = "SHELL=/bin/bash\n" + block + "\n"

    once = replace_managed_cron(existing, block)
    twice = replace_managed_cron(once, block)

    assert once == twice
    assert once.count("# BEGIN LOFARB COLLECTOR") == 1
    assert "041_jsl_cloud_shares.py" in once
    assert "cloud_siphon.py --kind fx" in once
    assert "cloud_siphon.py --kind futures" in once


def test_replace_managed_cron_removes_windows_crlf_block():
    block = build_cron_block(
        "/home/ubuntu/LOFarb",
        "/home/ubuntu/LOFarb/siphon_data",
        "/home/ubuntu/LOFarb/.venv/bin/python",
    )
    existing = block.replace("\n", "\\r\n")

    updated = replace_managed_cron(existing, block)

    assert updated.count("# BEGIN LOFARB COLLECTOR") == 1
    assert "\\r" not in updated


def test_normalize_share_symbol():
    assert normalize_share_symbol("162411") == "162411"
    assert normalize_share_symbol("501018") == "sh501018"
    assert normalize_share_symbol("sh501018") == "sh501018"
    assert normalize_share_symbol("") is None


def test_has_vps_auth_allows_agent_without_password():
    assert has_vps_auth("101.34.73.38", "ubuntu", password=None, key_path=None, allow_agent=True)


def test_has_vps_auth_rejects_missing_host_or_user():
    assert not has_vps_auth(None, "ubuntu", allow_agent=True)
    assert not has_vps_auth("101.34.73.38", None, allow_agent=True)
