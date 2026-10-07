# Whale 原廠遠端維護 VPN（vpn.whaleforensics.com）

Keel Edge 3／Mini 3 開機後透過 WireGuard 主動連回這台伺服器。原廠工程師經由 VPN，用工程師 CA 簽的短效 SSH 憑證登入維修。
這個目錄是伺服器的完整安裝包。伺服器私鑰在主機上產生，不會出現在這個 repo 或任何 session 裡。

```
infra/whale-vpn/
├── bootstrap.sh                  一次性安裝（可重跑）
├── files/
│   ├── nftables.conf.in          防火牆（隔離規則都在這裡）
│   ├── whale-vpn-peer            加入／撤銷／列出 peer（每次操作都記錄）
│   ├── whale-vpn-session-log     每分鐘記錄 VPN 連線起訖
│   ├── whale-vpn-connlog         查「誰在什麼時間連到哪台機器」
│   └── …                         rsyslog／logrotate／systemd／sysctl／sshd 設定
└── templates/
    ├── engineer-laptop.conf      工程師筆電設定範本
    └── machine-client.conf       機器端設定（產品端對齊用）
```

## 架構與規則

| 項目 | 值 |
|---|---|
| 伺服器 VPN 位址 | `10.8.0.1`（介面 `10.8.0.1/23`，涵蓋兩個網段） |
| 客戶機器 | `10.8.0.0/24`，每台一個 /32（`10.8.0.2`–`.254`） |
| 工程師筆電 | `10.8.1.0/24`，每台一個 /32（`10.8.1.1`–`.254`） |
| 對外開放 | 只有 UDP 51820。TCP 22 只允許 `ADMIN_SSH_CIDRS` 內的來源 |
| 唯一允許的轉送 | 工程師 → 機器，TCP 22 |
| 一律丟棄 | 機器↔機器、機器→工程師、工程師→工程師、任何人經 VPN 上外網、非 22 埠 |
| NAT | 不做，沒有 nat 表 |

WireGuard 的 cryptokey routing 保證封包來源 IP 一定是該 peer 登錄的 /32，所以「來源 IP = 哪台機器／哪位工程師」是可信的。
IP 撤銷後**不回收**，連線紀錄裡的 IP 永遠對得回當時的 peer。

## 1. 開主機

- 獨立的小主機：1 vCPU、1 GB RAM、Debian 12（建議）或 Ubuntu 24.04。
  **不要**跟 sun（KYC）、river、mars 放在同一台，也不要動它們的設定。
- 綁一個**固定公網 IP**（Elastic IP／Reserved IP／Floating IP 等）。
- 主機商層級的防火牆（Security Group 等）同樣只開：
  - UDP 51820 ← 0.0.0.0/0（機器在各客戶機關，來源不固定）
  - TCP 22 ← 管理者的固定 IP
- 建立時放入管理者的 SSH 公鑰。

## 2. DNS（Cloudflare）

| Type | Name | Content | Proxy status | TTL |
|---|---|---|---|---|
| A | `vpn` | 主機的固定公網 IP | **DNS only（灰雲）** | Auto |

必須是灰雲。WireGuard 是 UDP，Cloudflare 代理不了，開橘雲機器會連不上。
主機沒設 IPv6 的話，不要加 AAAA。

## 3. 安裝

```bash
# 從管理者電腦
scp -r infra/whale-vpn admin@<公網IP>:~/
ssh admin@<公網IP>
sudo ADMIN_SSH_CIDRS="<管理者IP>/32" ~/whale-vpn/bootstrap.sh
```

- `ADMIN_SSH_CIDRS` 必填，逗號分隔，可以混用 IPv4 和 IPv6。
  如果你目前的 SSH 來源不在清單內，腳本會拒絕執行，避免把自己鎖在外面。
- 腳本會：安裝套件 → sysctl（只開 IPv4 轉送，關掉 redirect）→ nftables → **在本機產生伺服器金鑰** →
  啟動 `wg-quick@wg0` → 設定連線紀錄 → 自動安全更新 → 管理 SSH 改成只能用金鑰。
- 結束時印出**伺服器公鑰**和目前對外監聽的埠。之後要再看公鑰：`sudo cat /etc/wireguard/server.pub`。
- 要修改管理來源：帶新的 `ADMIN_SSH_CIDRS` 重跑一次即可。重跑時沿用原本的伺服器金鑰，peer 也不受影響。

## 4. 驗收

在伺服器上：

```bash
sudo nft list ruleset          # 只有 inet whale_vpn 這張表，沒有 nat
sudo ss -tulpn                 # 只有 udp :51820 和 tcp :22
sudo wg show
dig +short vpn.whaleforensics.com   # = 固定公網 IP
```

第一台工程師筆電和第一台機器登錄後：

| 從 | 動作 | 預期 |
|---|---|---|
| 工程師筆電 | `nc -vz 10.8.0.N 22` | 通 |
| 工程師筆電 | `ping 10.8.0.N`、`nc -vz 10.8.0.N 80` | 不通 |
| 機器 | `nc -vz <另一台機器> 22`、`nc -vz 10.8.1.N 22` | 不通 |
| 外部任一 IP | `nc -vz <公網IP> 22` | 不通（管理來源以外） |
| 伺服器 | `sudo whale-vpn-connlog` | 看得到剛才那筆工程師 → 機器的連線 |

防火牆規則在 repo 裡用 network namespace 驗證過：以上 13 種組合全部符合預期。

## 5. 加入／撤銷 peer

所有操作都必須用 `sudo`。每次操作都會寫進 `/var/log/whale-vpn/peer-admin.log`（append-only，不輪替，永久保存）和 syslog，
內容包含時間、執行者（sudo 前的帳號）、名稱、IP、公鑰和備註。被拒絕的操作也會記錄。
請每位管理者使用自己的帳號登入，紀錄才分得出是誰做的。

**加入機器**：機器安裝時畫面會顯示它的 WireGuard 公鑰。

```bash
sudo whale-vpn-peer add machine keel-edge3-<序號> '<機器公鑰>' --note "<客戶機關> SN<序號>"
# → 已加入：machine keel-edge3-xxx → 10.8.0.2/32
sudo whale-vpn-peer show-config keel-edge3-<序號>   # 印出機器端該填的值（Address／伺服器公鑰）
```

指定 IP 用 `--ip 10.8.0.N`。若 IP 曾被分配過（包括已撤銷的），會被拒絕。

**加入工程師筆電**：

```bash
sudo whale-vpn-peer add engineer <名字>-<設備> '<筆電公鑰>' --note "<誰的哪台電腦>"
# 直接印出已填好位址與伺服器公鑰的筆電設定，只差筆電自己的私鑰
```

**撤銷**（機器報廢、合約結束、筆電遺失、離職、金鑰外洩）：

```bash
sudo whale-vpn-peer revoke <名稱|IP|公鑰> --reason "<原因>"
```

撤銷立即生效（`wg syncconf`，其他連線不受影響）。被撤銷的公鑰和 IP 都不能再使用。
機器重新安裝時要產生新金鑰，再用新名稱（例如加 `-r2`）登錄一次。

**查詢**：

```bash
sudo whale-vpn-peer list          # 使用中的 peer
sudo whale-vpn-peer list --all    # 包括已撤銷的
```

登錄簿 `/var/lib/whale-vpn/peers.tsv` 是唯一的資料來源。`/etc/wireguard/wg0.conf` 由它和 `wg0.interface` 產生，**請勿手動修改**。
如果懷疑執行中的設定和登錄簿不一致：`sudo whale-vpn-peer sync`。

## 6. 連線紀錄（保留 24 個月）

| 檔案 | 內容 |
|---|---|
| `/var/log/whale-vpn/ssh-connections.log` | 每一筆工程師 → 機器的 SSH 新連線：時間、來源工程師 IP、目的機器 IP（由 nftables 記錄） |
| `/var/log/whale-vpn/sessions.log` | 每個 peer 的 VPN 連線起訖、來源公網 IP:port、IP 變動 |
| `/var/log/whale-vpn/forward-drops.log` | 被擋下的轉送（限速），例如機器互連、機器嘗試上網 |
| `/var/log/whale-vpn/peer-admin.log` | 所有 peer 管理操作（不輪替） |

```bash
sudo whale-vpn-connlog                         # 誰在什麼時間連到哪台機器（含壓縮的舊檔）
sudo whale-vpn-connlog --since 2026-10-01
sudo whale-vpn-connlog keel-edge3-001          # 只看某台機器或某位工程師
```

紀錄每月輪替並壓縮，保留 24 份（需求是至少 1 年），檔名形如 `ssh-connections.log-202610.gz`。
時間格式為 RFC 3339，含時區；時間由 systemd-timesyncd 或 chrony 校正。

**建議**：紀錄只存在這台主機上，主機毀損就會遺失。請另外每天把 `/var/log/whale-vpn/` 和 `/var/lib/whale-vpn/peers.tsv`
備份到其他地方（唯讀、可稽核的儲存位置）。**不要**備份 `/etc/wireguard/wg0.interface`（伺服器私鑰）。

## 7. 維運注意

- 伺服器私鑰只存在 `/etc/wireguard/wg0.interface` 和由它產生的 `wg0.conf`，兩者權限都是 0600。不要複製、不要貼給任何人或任何 session。
- 這台伺服器不保存、也不需要工程師 SSH CA 私鑰。CA 私鑰在 Chris 的 Mac 上。
- 防火牆要改，請改 `files/nftables.conf.in` 後重跑 `bootstrap.sh`，不要直接編輯 `/etc/nftables.conf`。
- 主機損毀需要重建時，會產生新的伺服器金鑰，所有機器和筆電都要更新伺服器公鑰。登錄簿可以從備份還原。
  如果想避免這種情況而開啟主機商的磁碟快照，私鑰就會存在快照裡，等於離開了這台主機。這需要另外做決定，預設不開。
