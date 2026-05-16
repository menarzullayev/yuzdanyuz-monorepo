# Cloudflare — DNS + WAF + Under Attack Mode

Edge defense layer (L1.5 — before K8s ingress, before app middleware).

Manifests: [`terraform/cloudflare/`](../../../../terraform/cloudflare/)

## Apply

```bash
cd terraform/cloudflare/
cp terraform.tfvars.example terraform.tfvars
# Fill: cloudflare_api_token, zone_id, domain, origin_ip

terraform init
terraform plan
terraform apply
```

API token permissions (Cloudflare → My Profile → API Tokens):
- Zone → DNS → Edit
- Zone → WAF → Edit
- Zone → Page Rules → Edit
- Zone → Zone Settings → Edit
- Zone → Zone → Read

## Resurslar yaratiladi

### DNS (proxied — origin IP yashirin)
- `@` (apex) → `origin_ip`, proxied
- `api.<domain>` → `origin_ip`, proxied
- `*.<domain>` → `origin_ip`, proxied (white-label tenants)

### Zone settings
- SSL: **strict** (origin must serve valid cert — cert-manager + Let's Encrypt)
- TLS: 1.2 minimum, 1.3 enabled
- Always Use HTTPS, HSTS rewrites, HTTP/3
- WebSocket support
- Brotli, Early Hints
- Security level: medium (bot fight on)

### Page Rules
- `*<domain>/api/*` → cache bypass
- `*<domain>/static/*` → cache 30 days

### Custom WAF rules
- Block recon scanners (sqlmap, nikto, masscan)
- Challenge suspicious `/api/auth/login` (threat_score > 14)
- Geo-fence `/admin` to UZ + RU + KZ + KG + TJ + TM
- Challenge TOR exit nodes on API

### Rate limiting (zone level)
- 300 req/min per IP on `/api/*` → challenge for 10min

## Under Attack Mode runbook

DDoS chiqsa Cloudflare dashboard'dan **Under Attack Mode** yoqing:

```bash
# Or via API
curl -X PATCH "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/settings/security_level" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data '{"value":"under_attack"}'
```

Bu rejimda har bir visitor 5 sekundlik JS challenge'dan o'tadi. Bot'lar
o'tolmaydi, lekin real user'lar 5s kutadi.

Hujum tugagandan keyin:
```bash
curl -X PATCH "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/settings/security_level" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data '{"value":"medium"}'
```

## Origin IP yashirish

Cloudflare proxy faqat HTTP traffic'ni proxy qiladi. Agar origin server boshqa
port'larda (SMTP, SSH) ham public bo'lsa, real IP topiladi.

Defenses:
1. K8s Ingress LoadBalancer faqat 80/443 ochiq
2. SSH faqat allowed VPN/bastion'dan
3. PostgreSQL/Redis hech qachon public emas (private subnet)
4. Rate limit egress (faqat Cloudflare IP'larga response qaytarsin):

```bash
# Iptables — only allow ingress from Cloudflare
for ip in $(curl -s https://www.cloudflare.com/ips-v4); do
    iptables -A INPUT -p tcp -m multiport --dports 80,443 -s "$ip" -j ACCEPT
done
iptables -A INPUT -p tcp -m multiport --dports 80,443 -j DROP
```

## TLS — origin certificate

Production — Let's Encrypt via cert-manager (ingress-nginx + ClusterIssuer).
Cloudflare bilan **strict** SSL → origin valid cert kerak.

Yoki Cloudflare Origin CA — 15-yillik Cloudflare-issued cert
(faqat Cloudflare proxy'dan accept qilinadi):
1. Cloudflare → SSL/TLS → Origin Server → Create Certificate
2. K8s Secret yaratish:
   ```bash
   kubectl -n yuzdanyuz-prod create secret tls yuzdanyuz-tls \
     --cert=origin.pem --key=origin.key
   ```
3. Ingress `tls.secretName: yuzdanyuz-tls`

## Audit

```bash
# Recent firewall events
curl "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/security/events?limit=50" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}"

# Top attacked endpoints
# Cloudflare → Analytics → Security → Top Attacked URLs
```
