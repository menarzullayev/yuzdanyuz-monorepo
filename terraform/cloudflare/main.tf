# Cloudflare WAF + DNS + Page Rules for ${PROD_DOMAIN}.
# Apply: terraform init && terraform apply
# Required env: TF_VAR_cloudflare_api_token, TF_VAR_zone_id, TF_VAR_origin_ip
#
# Cloudflare API token must have permissions:
#   Zone.DNS:Edit, Zone.WAF:Edit, Zone.Page Rules:Edit, Zone.Settings:Edit

terraform {
  required_version = ">= 1.5"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.40"
    }
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

# ── DNS — A records (proxied through Cloudflare) ──────────────────────────────

resource "cloudflare_record" "root" {
  zone_id = var.zone_id
  name    = "@"
  value   = var.origin_ip
  type    = "A"
  ttl     = 1            # Auto when proxied
  proxied = true
  comment = "Apex — Cloudflare proxy hides origin IP (DDoS protection)"
}

resource "cloudflare_record" "api" {
  zone_id = var.zone_id
  name    = "api"
  value   = var.origin_ip
  type    = "A"
  ttl     = 1
  proxied = true
  comment = "REST + WebSocket (sub: api.${var.domain})"
}

resource "cloudflare_record" "wildcard" {
  zone_id = var.zone_id
  name    = "*"
  value   = var.origin_ip
  type    = "A"
  ttl     = 1
  proxied = true
  comment = "White-label tenant subdomains (*.${var.domain})"
}

# ── Zone Settings — TLS, HTTP/3, browser integrity, security level ────────────

resource "cloudflare_zone_settings_override" "settings" {
  zone_id = var.zone_id
  settings {
    ssl                      = "strict"        # Origin must serve valid cert (cert-manager)
    always_use_https         = "on"
    automatic_https_rewrites = "on"
    min_tls_version          = "1.2"
    tls_1_3                  = "on"
    opportunistic_encryption = "on"
    http3                    = "on"
    websockets               = "on"
    brotli                   = "on"
    early_hints              = "on"
    security_level           = "medium"        # bot fight + JS challenge for suspicious
    challenge_ttl            = 1800
    browser_check            = "on"
    privacy_pass             = "on"
    server_side_exclude      = "on"
    hotlink_protection       = "off"           # student avatars must load on mobile
  }
}

# ── Page Rule — admin / signup must always be on origin (no cache) ────────────

resource "cloudflare_page_rule" "no_cache_dynamic" {
  zone_id  = var.zone_id
  target   = "*${var.domain}/api/*"
  priority = 10
  status   = "active"
  actions {
    cache_level = "bypass"
  }
}

resource "cloudflare_page_rule" "cache_static" {
  zone_id  = var.zone_id
  target   = "*${var.domain}/static/*"
  priority = 5
  status   = "active"
  actions {
    cache_level    = "cache_everything"
    edge_cache_ttl = 2592000   # 30 days
    browser_cache_ttl = 2592000
  }
}

# ── WAF custom rules ──────────────────────────────────────────────────────────

resource "cloudflare_ruleset" "waf_custom" {
  zone_id     = var.zone_id
  name        = "yuzdanyuz custom WAF"
  description = "App-specific WAF rules"
  kind        = "zone"
  phase       = "http_request_firewall_custom"

  # Block known bad bots (curl/wget/nikto/sqlmap with no real UA)
  rules {
    action      = "block"
    expression  = "(http.user_agent contains \"sqlmap\") or (http.user_agent contains \"nikto\") or (http.user_agent contains \"masscan\")"
    description = "Block recon scanners"
    enabled     = true
  }

  # Rate-limit /api/auth/login/ — handled by app too, but defense-in-depth
  rules {
    action      = "managed_challenge"
    expression  = "(http.request.uri.path contains \"/api/auth/login\") and (cf.threat_score gt 14)"
    description = "Challenge suspicious login attempts"
    enabled     = true
  }

  # Geo-fence admin endpoints to UZ + RU + CIS
  rules {
    action     = "block"
    expression = "(http.request.uri.path contains \"/admin\") and (ip.geoip.country ne \"UZ\") and (ip.geoip.country ne \"RU\") and (ip.geoip.country ne \"KZ\") and (ip.geoip.country ne \"KG\") and (ip.geoip.country ne \"TJ\") and (ip.geoip.country ne \"TM\")"
    description = "Restrict /admin to CIS region"
    enabled     = true
  }

  # Block known TOR exit nodes from sensitive paths
  rules {
    action      = "managed_challenge"
    expression  = "(ip.geoip.is_tor) and (http.request.uri.path contains \"/api/\")"
    description = "Challenge TOR users on API"
    enabled     = true
  }
}

# ── Rate Limiting (zone-level, in addition to app-level RateLimitMiddleware) ──

resource "cloudflare_rate_limit" "api_burst" {
  zone_id   = var.zone_id
  threshold = 300
  period    = 60
  match {
    request {
      url_pattern = "*${var.domain}/api/*"
      schemes     = ["HTTPS"]
      methods     = ["GET", "POST", "PUT", "DELETE", "PATCH"]
    }
    response {
      statuses = [200, 201, 401, 403, 429]
    }
  }
  action {
    mode    = "challenge"
    timeout = 600
  }
  description = "Per-IP burst limit on /api/*"
}
