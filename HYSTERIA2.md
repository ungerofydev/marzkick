# Hysteria 2 support

Marzban serves Hysteria 2 through the **native Xray-core `hysteria` inbound** (no separate
Hysteria process). It needs an Xray core that contains the Hysteria inbound
(`xray run` with `"protocol": "hysteria"`, Xray 26.x+).

## Server side

Add an inbound to the Xray config (Core Settings in the dashboard). Users are added by
Marzban at runtime, keep `users` empty:

```json
{
  "tag": "Hysteria2 UDP",
  "listen": "0.0.0.0",
  "port": 8443,
  "protocol": "hysteria",
  "settings": { "version": 2, "users": [] },
  "streamSettings": {
    "network": "hysteria",
    "security": "tls",
    "tlsSettings": {
      "alpn": ["h3"],
      "certificates": [
        { "certificateFile": "/path/fullchain.pem", "keyFile": "/path/privkey.pem" }
      ]
    },
    "hysteriaSettings": { "version": 2 },
    "finalmask": {
      "udp": [{ "type": "salamander", "settings": { "password": "CHANGE-ME" } }]
    }
  }
}
```

* The port is **UDP**; open it in the firewall.
* `finalmask.udp` with a `salamander` entry is optional obfuscation; when present it is
  published to clients automatically (`obfs=salamander`, `obfs-password`).
* In Marzban the proxy type is called `hysteria2` (Xray calls the inbound `hysteria`).
  Each user has one secret, `auth`, which can be set or left empty to be generated.

## Subscriptions

| Format | Result |
| --- | --- |
| v2ray / base64 | `hysteria2://AUTH@host:port/?sni=…&obfs=salamander&obfs-password=…#remark` |
| v2ray-json | Xray `hysteria` outbound (`network: hysteria`, `hysteriaSettings.auth`, `finalmask`) |
| Clash Meta | `type: hysteria2` proxy |
| sing-box | `hysteria2` outbound |
| Clash (plain), Outline | skipped (not supported by those clients) |

SNI comes from the certificate SANs unless the host overrides it. ALPN defaults to `h3`.

## Host Settings

The inbound shows up in *Host Settings* like any other inbound. Relevant fields: address,
port, SNI, ALPN, *Allow Insecure*. For self-signed certificates enable *Allow Insecure*: Marzban
also computes the certificate's SHA-256 fingerprint and publishes it as `pinSHA256`
(link), `pinnedPeerCertSha256` (v2ray-json, because recent Xray cores removed
`allowInsecure`) and `fingerprint` (Clash Meta), so clients pin that exact certificate
instead of skipping verification entirely.
With a CA-issued certificate (for example Let's Encrypt for a real domain) nothing is pinned or skipped: use
the domain as the host *Address* and *SNI*, point the inbound's `certificateFile` / `keyFile` at the
certificate and restart Xray after each renewal (a certbot `deploy` hook). On UDP 443 without salamander the
traffic looks like ordinary HTTP/3 to the same domain.

## Limitations

* Xray has no API to drop an established Hysteria session. A disabled/expired/limited user is
  rejected for **new** sessions, but an already connected client keeps its QUIC session until
  it reconnects.
* Node support (`marzban-node`) requires a node Xray core with the Hysteria inbound.
* Port hopping is not configured automatically; if the inbound `port` is a range such as `"20000-50000,443"`, it is
  published as `mport` in share links (other formats use its first port).
