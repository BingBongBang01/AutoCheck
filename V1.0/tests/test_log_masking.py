"""외부 공유용 마스킹(engine/log_masking.py) — IMPROVEMENT_PLAN 1-7.

마스킹 결과는 고객사 밖으로 나가는 파일이다. 규칙이 놓친 값은 그대로 유출된다. 그런데 이
모듈에는 테스트가 하나도 없었고, 실제로 아래 값들이 가려지지 않았다:
  - IPv6 주소
  - `snmp-server host <ip> version 2c <community>` 의 커뮤니티
  - TACACS/RADIUS key, OSPF/NTP authentication-key, key-string, SNMPv3 auth/priv, IPsec PSK
  - cp949 로 저장된 레거시 로그(UTF-8 로만 읽어서 한글이 깨진 채 저장됐다)

픽스처는 EOS/IOS/NX-OS 설정 문법을 섞은 것이다. 비밀값은 일부러 눈에 띄는 문자열로 넣어
"마스킹 결과에 이 문자열이 하나도 없어야 한다"로 검사한다.
"""
import pytest

from engine import log_masking as lm

CONFIG = """\
! device: Core1 (vEOS-lab, EOS-4.28.0F)
hostname Core1
username admin privilege 15 role network-admin secret sha512 $6$abcDEF123$xyzXYZ987654321
enable secret 5 $1$mERr$hx5rVt7rPNoS4wqbXKX7m0
tacacs-server host 10.1.1.10 key 7 070C285F4D06
tacacs-server key 7 1511021F0725
radius-server host 10.1.1.11 auth-port 1812 acct-port 1813 key 7 02050D480809
tacacs server TAC1
 address ipv4 10.1.1.12
 key 7 0822455D0A16
snmp-server community PUBLICRO ro
snmp-server host 10.1.1.20 version 2c TRAPCOMM
snmp-server host 10.1.1.21 vrf MGMT version 2c TRAPVRF
snmp-server group G1 v3 priv read V1
snmp-server user u1 G1 v3 auth sha AUTHPASS123 priv aes 128 PRIVPASS456
ntp authentication-key 1 md5 7 NTPKEY111
interface Ethernet1
   description core_agg_mlag
   ip ospf authentication-key 7 OSPFKEY222
   ip ospf message-digest-key 1 md5 7 OSPFMD5333
   vrrp 1 authentication text VRRPTXT444
key chain KC1
 key 1
  key-string 7 KEYSTR555
crypto isakmp key ISAKMP666 address 203.0.113.1
 pre-shared-key local PSK777
router bgp 65001
   neighbor 10.0.0.2 password 7 BGPPW888
   neighbor 10.0.0.2 send-community extended
ipv6 address 2001:db8:10::1/64
"""

SECRETS = [
    "abcDEF123", "xyzXYZ987654321", "hx5rVt7rPNoS4wqbXKX7m0", "070C285F4D06", "1511021F0725",
    "02050D480809", "0822455D0A16", "PUBLICRO", "TRAPCOMM", "TRAPVRF", "AUTHPASS123",
    "PRIVPASS456", "NTPKEY111", "OSPFKEY222", "OSPFMD5333", "VRRPTXT444", "KEYSTR555",
    "ISAKMP666", "PSK777", "BGPPW888",
]

# 비밀값이 아닌 구조 — 마스킹이 과하게 먹어서 뜻이 사라지면 안 된다.
UNTOUCHED_LINES = [
    "interface Ethernet1", "   description core_agg_mlag", "key chain KC1", " key 1",
    "snmp-server group G1 v3 priv read V1", "   neighbor **.*.*.* send-community extended",
]

ALL_KEYS = set(lm.MASK_KEY_ORDER)


def test_every_secret_is_masked_with_all_rules():
    masked = lm.apply_masking(CONFIG, ALL_KEYS)
    leaked = [secret for secret in SECRETS if secret in masked]
    assert leaked == [], f"가려지지 않은 비밀값: {leaked}"
    assert lm.find_unmasked_secrets(masked) == {}


def test_masking_preserves_structure_and_line_lengths():
    masked = lm.apply_masking(CONFIG, ALL_KEYS)
    original_lines, masked_lines = CONFIG.splitlines(), masked.splitlines()
    assert [len(line) for line in masked_lines] == [len(line) for line in original_lines]
    for line in UNTOUCHED_LINES:
        assert line in masked_lines, f"과잉 마스킹: {line!r}"


def test_secret_rules_survive_generic_rules_running_first_in_display_order():
    """os_version('version 2c' → 'version **')가 먼저 돌아도 trap 커뮤니티를 놓치지 않는다."""
    masked = lm.apply_masking("snmp-server host 10.1.1.20 version 2c TRAPCOMM\n",
                              {"os_version", "ip_address", "snmp_community"})
    assert "TRAPCOMM" not in masked
    assert "version" in masked, "키워드 'version' 을 비밀값으로 오인해 가렸다"


def test_each_new_secret_rule_on_its_own():
    """auth_key 만 골라도 그 범주의 키가 전부 가려진다(다른 규칙에 기대지 않는다)."""
    masked = lm.apply_masking(CONFIG, {"auth_key"})
    for secret in ["070C285F4D06", "1511021F0725", "02050D480809", "0822455D0A16", "AUTHPASS123",
                   "PRIVPASS456", "NTPKEY111", "OSPFKEY222", "OSPFMD5333", "VRRPTXT444",
                   "KEYSTR555", "ISAKMP666", "PSK777"]:
        assert secret not in masked, secret
    community = lm.apply_masking(CONFIG, {"snmp_community"})
    assert "TRAPCOMM" not in community and "TRAPVRF" not in community and "PUBLICRO" not in community


def test_leftover_scan_warns_when_secret_categories_were_not_selected():
    """IP 만 골라 마스킹한 결과를 그대로 공유하면 비밀값이 나간다 — 그걸 알려야 한다."""
    masked = lm.apply_masking(CONFIG, {"ip_address"})
    found = lm.find_unmasked_secrets(masked)
    assert set(found) == set(lm.SECRET_KEYS) - {"private_key", "secret_token"}
    assert "Auth Key" in lm.describe_unmasked(found)
    assert lm.describe_unmasked({}) is None


def test_leftover_scan_does_not_cross_line_breaks():
    """줄 끝의 'password' 다음 줄 첫 단어를 비밀값으로 오인하면 거짓 경고가 난다."""
    log = "% Login failed for user admin: invalid password\nCore1#show version\n"
    assert lm.find_unmasked_secrets(log) == {}
    assert "Core1#show version" in lm.apply_masking(log, {"password_hash"})


@pytest.mark.parametrize("address", [
    "2001:db8:10::1", "fe80::1", "::1", "2001:0db8:0000:0000:0000:ff00:0042:8329", "2001:db8::",
])
def test_ipv6_addresses_are_masked(address):
    masked = lm.apply_masking(f"peer {address} up", {"ip_address"})
    assert address not in masked
    assert len(masked) == len(f"peer {address} up")
    assert ":" in masked, "구분자(:)는 남겨 형태를 알아볼 수 있어야 한다"


@pytest.mark.parametrize("text", ["mac 00:1c:73:aa:bb:cc", "at 10:15:30", "Et1: up", "dead:beef"])
def test_ipv6_rule_ignores_mac_and_time(text):
    assert lm.apply_masking(text, {"ip_address"}) == text


def test_json_style_api_key_is_masked():
    masked = lm.apply_masking('{"api_key": "sk-live-ABC123", "x": 1}\napi_key=TOKEN999\n', {"secret_token"})
    assert "sk-live-ABC123" not in masked and "TOKEN999" not in masked


def test_run_masking_reads_cp949_logs_and_reports_leftovers(tmp_path):
    source, output = tmp_path / "raw", tmp_path / "masked"
    source.mkdir()
    (source / "Core1.txt").write_bytes(
        "! 위치: 서울 본사 3층\nsnmp-server community PUBLICRO ro\nenable secret 5 HASHVALUE\n".encode("cp949"))

    results = lm.run_masking(str(source), str(output), {"snmp_community"})

    text = (output / "Core1_masked.txt").read_text(encoding="utf-8")
    assert "서울 본사" in text, "cp949 한글이 깨졌다"
    assert "PUBLICRO" not in text
    assert results[0]["unmasked_secrets"] == {"password_hash": 1}, "선택하지 않은 비밀 범주는 남았다고 알려야 한다"
