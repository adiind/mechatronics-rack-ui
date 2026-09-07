# Rope firmware (ESPHome) — flash from the Pi

Generated from `reference/ledwall-node02.yaml` with only two changes per node:
the node number in name/topics, and `color_correct` 55% -> 80%.

Node IPs (mDNS, 2026-09-06): node02 10.106.30.54 · node03 10.106.3.174 ·
node04 10.106.3.163 · node05 10.106.3.105 · node06 10.106.5.3 · node07 10.106.3.27 ·
node08 10.106.2.173. All answer on OTA port 3232.

    cp secrets.yaml.example secrets.yaml && chmod 600 secrets.yaml   # fill in real values
    ~/esphome-venv/bin/esphome run ledwall-node03.yaml --device 10.106.3.174 --no-logs

## Flash log

2026-09-06 16:59–17:19 CDT: all seven nodes OTA-flashed from this Pi at
`color_correct` 80% (ESPHome 2026.8.2, `~/esphome-venv`). Every node came back
online and acked within seconds; the studio's rope-health view read
"confirmed 7/7" throughout. Wi-Fi (Device-Northwestern) is an open network, so
`wifi_password` is legitimately empty. `secrets.yaml` (mode 600) is real and
must stay out of git and chat.

Incremental compile + OTA takes about 3 min 15 s per node on the Pi; do them
sequentially, parallel compiles exhaust the 4 GB of RAM.

Flash ONE node first and check connector temperature at full white before the rest.
`secrets.yaml` is gitignored-by-convention: never commit or paste it.
