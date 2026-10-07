#!/usr/bin/env bash
# Whale 原廠遠端維護 VPN 伺服器：一次性安裝（可重跑）。
#
# 在「新開的、專用的」Debian 12 / Ubuntu 24.04 主機上，以 root 執行：
#   sudo ADMIN_SSH_CIDRS="203.0.113.10/32,198.51.100.0/28" ./bootstrap.sh
#
# ADMIN_SSH_CIDRS：允許 SSH 管理這台伺服器的來源（逗號分隔，可含 IPv6）。必填。
#
# 伺服器私鑰在這台主機上產生，只存在 /etc/wireguard/wg0.interface（0600），
# 本腳本不會把它印出或傳到任何地方。結束時只印出伺服器公鑰。
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
F="$HERE/files"
WG_IF=wg0
WG_PORT="${WG_PORT:-51820}"
VPN_FQDN="${VPN_FQDN:-vpn.whaleforensics.com}"

die() { echo "錯誤：$*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "請用 root 執行"
[[ -n ${ADMIN_SSH_CIDRS:-} ]] || die "必須設定 ADMIN_SSH_CIDRS（允許 SSH 管理本機的來源）"
. /etc/os-release
[[ $ID == debian || $ID == ubuntu ]] || die "只支援 Debian / Ubuntu（偵測到 $ID）"

step "安裝套件"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
	wireguard-tools nftables rsyslog logrotate unattended-upgrades python3 iproute2 util-linux bsdextrautils e2fsprogs

step "檢查管理來源 ADMIN_SSH_CIDRS"
# 輸出三行：IPv4 清單、IPv6 清單、目前 SSH 來源是否在清單內（避免把自己鎖在外面）
ssh_src=${SSH_CONNECTION:-}
ssh_src=${ssh_src%% *}
# 經過 sudo 時 SSH_CONNECTION 會被清掉，改從登入紀錄取來源
[[ -n $ssh_src ]] || ssh_src=$(who -m 2>/dev/null | sed -n 's/.*(\([0-9a-fA-F:.]*\)).*/\1/p')
cidr_out=$(python3 - "$ADMIN_SSH_CIDRS" "$ssh_src" <<'PY'
import ipaddress, sys
raw, me = sys.argv[1], sys.argv[2]
nets = []
for item in filter(None, (s.strip() for s in raw.split(","))):
    try:
        nets.append(ipaddress.ip_network(item, strict=False))
    except ValueError:
        sys.exit(f"不是合法的位址或網段：{item}")
for n in nets:
    if n.prefixlen == 0:
        sys.exit(f"不允許整個網際網路：{n}")
print(", ".join(str(n) for n in nets if n.version == 4))
print(", ".join(str(n) for n in nets if n.version == 6))
status = "OK"
try:
    addr = ipaddress.ip_address(me) if me else None
except ValueError:
    addr = None
if addr:
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if not any(addr in n for n in nets):
        status = f"LOCKOUT {addr}"
print(status)
PY
) || die "ADMIN_SSH_CIDRS 格式錯誤"
v4=$(sed -n 1p <<<"$cidr_out") v6=$(sed -n 2p <<<"$cidr_out") me_status=$(sed -n 3p <<<"$cidr_out")
if [[ $me_status == LOCKOUT* && ${ALLOW_LOCKOUT:-0} != 1 ]]; then
	die "你目前的 SSH 來源（${me_status#LOCKOUT }）不在 ADMIN_SSH_CIDRS 內，套用後會被鎖在外面。確定要這樣做請加 ALLOW_LOCKOUT=1"
fi
[[ -n $v4 || -n $v6 ]] || die "ADMIN_SSH_CIDRS 解析後是空的"
echo "IPv4: ${v4:-（無）}"
echo "IPv6: ${v6:-（無）}"

step "停用其他防火牆管理工具（本機只用 nftables）"
for svc in ufw firewalld; do
	if systemctl is-enabled "$svc" >/dev/null 2>&1; then
		[[ $svc == ufw ]] && ufw --force disable || true
		systemctl disable --now "$svc" || true
		echo "已停用 $svc"
	fi
done

step "核心參數（只在 wg0 內轉送，不做 NAT）"
install -m 0644 "$F/90-whale-vpn.sysctl.conf" /etc/sysctl.d/90-whale-vpn.conf
sysctl -q --system

step "時間同步（連線紀錄的時間要準）"
if ! systemctl is-active --quiet chrony 2>/dev/null; then
	apt-get install -y -q --no-install-recommends systemd-timesyncd
	timedatectl set-ntp true
fi

step "防火牆"
elements() { [[ -n $1 ]] && echo "elements = { $1 }" || true; }
sed -e "s|__WG_PORT__|$WG_PORT|" \
	-e "s|__ADMIN_V4_ELEMENTS__|$(elements "$v4")|" \
	-e "s|__ADMIN_V6_ELEMENTS__|$(elements "$v6")|" \
	"$F/nftables.conf.in" >/etc/nftables.conf.new
nft -c -f /etc/nftables.conf.new
mv -f /etc/nftables.conf.new /etc/nftables.conf
chmod 0755 /etc/nftables.conf
nft -f /etc/nftables.conf
systemctl enable nftables

step "WireGuard 金鑰與介面"
install -d -m 0700 /etc/wireguard
IFACE=/etc/wireguard/$WG_IF.interface
if [[ -s $IFACE ]]; then
	priv=$(awk -F' = ' '$1 == "PrivateKey" { print $2 }' "$IFACE")
	echo "沿用既有伺服器金鑰"
else
	priv=$(wg genkey)
	echo "已在本機產生新的伺服器金鑰"
fi
(
	umask 077
	cat >"$IFACE.new" <<EOF
[Interface]
# 伺服器私鑰只存在這台主機。勿複製、勿進 repo。
PrivateKey = $priv
# /23 = 10.8.0.0–10.8.1.255：機器 10.8.0.0/24 + 工程師 10.8.1.0/24 都直接在 wg0 上
Address = 10.8.0.1/23
ListenPort = $WG_PORT
EOF
	mv -f "$IFACE.new" "$IFACE"
	wg pubkey <<<"$priv" >/etc/wireguard/server.pub
)
unset priv
chmod 0644 /etc/wireguard/server.pub

step "安裝管理腳本與紀錄設定"
install -m 0755 "$F/whale-vpn-peer" "$F/whale-vpn-session-log" "$F/whale-vpn-connlog" /usr/local/sbin/
install -d -m 0700 /var/lib/whale-vpn

LOG_OWNER=root
# Ubuntu 的 rsyslog 以 syslog 身分執行，需要能寫紀錄目錄
# shellcheck disable=SC2016
if grep -rqs '^\$PrivDropToUser syslog' /etc/rsyslog.conf /etc/rsyslog.d; then LOG_OWNER=syslog; fi
install -d -m 0750 -o "$LOG_OWNER" -g adm /var/log/whale-vpn
for f in ssh-connections.log forward-drops.log; do
	[[ -e /var/log/whale-vpn/$f ]] || install -m 0640 -o "$LOG_OWNER" -g adm /dev/null "/var/log/whale-vpn/$f"
done
[[ -e /var/log/whale-vpn/sessions.log ]] || install -m 0640 -o root -g adm /dev/null /var/log/whale-vpn/sessions.log
if [[ ! -e /var/log/whale-vpn/peer-admin.log ]]; then
	install -m 0640 -o root -g adm /dev/null /var/log/whale-vpn/peer-admin.log
fi
# peer 操作紀錄只能附加、不能改寫或刪除
chattr +a /var/log/whale-vpn/peer-admin.log || echo "警告：此檔案系統不支援 chattr +a"

install -m 0644 "$F/30-whale-vpn.rsyslog.conf" /etc/rsyslog.d/30-whale-vpn.conf
sed "s|__LOG_OWNER__|$LOG_OWNER|g" "$F/whale-vpn.logrotate.in" >/etc/logrotate.d/whale-vpn
chmod 0644 /etc/logrotate.d/whale-vpn
logrotate -d /etc/logrotate.d/whale-vpn >/dev/null 2>&1 || die "logrotate 設定有誤"
systemctl enable rsyslog
systemctl restart rsyslog

install -m 0644 "$F/whale-vpn-session-log.service" "$F/whale-vpn-session-log.timer" /etc/systemd/system/
systemctl daemon-reload

step "啟動 WireGuard"
# 依登錄簿產生 wg0.conf（第一次是沒有 peer 的空設定）
WHALE_VPN_NO_APPLY=1 /usr/local/sbin/whale-vpn-peer sync
if systemctl is-active --quiet "wg-quick@$WG_IF"; then
	/usr/local/sbin/whale-vpn-peer sync
else
	systemctl enable --now "wg-quick@$WG_IF"
fi
systemctl enable --now whale-vpn-session-log.timer

step "自動安全更新"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

step "管理 SSH 只用金鑰"
has_key=0
for u in root ${SUDO_USER:-}; do
	home=$(getent passwd "$u" | cut -d: -f6)
	[[ -s $home/.ssh/authorized_keys ]] && has_key=1
done
if ((has_key)); then
	install -m 0644 "$F/10-whale-vpn.sshd.conf" /etc/ssh/sshd_config.d/10-whale-vpn.conf
	sshd -t
	systemctl reload ssh 2>/dev/null || systemctl reload sshd
else
	echo "警告：找不到 authorized_keys，先不關閉密碼登入。放好金鑰後重跑本腳本。"
fi

step "完成"
local4=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{ for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1) }')
dns4=$(getent ahostsv4 "$VPN_FQDN" 2>/dev/null | awk 'NR == 1 { print $1 }')
cat <<EOF

伺服器公鑰：     $(cat /etc/wireguard/server.pub)
本機 IPv4：      ${local4:-未知}
$VPN_FQDN 解析： ${dns4:-（尚未設定）}
（雲端主機常是 1:1 NAT，本機 IPv4 可能是私有位址；公網 IP 以主機商後台的固定 IP 為準，
  DNS 必須指向那個公網 IP，且 Cloudflare 必須是「DNS only／灰雲」。）

目前對外監聽：
$(ss -tulpnH | awk '{ print "  " $1, $5, $7 }')

預期只有：udp *:$WG_PORT（WireGuard）與 tcp *:22（sshd，已被防火牆限制來源）。
其他服務若出現在上面，請確認這是一台專用主機。
EOF
wg show "$WG_IF"
