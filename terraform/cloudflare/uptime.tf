# External uptime probes (audit G07). Cloudflare probes the origin from several
# regions outside our cluster, so they still alert when the whole cluster, the
# ingress or Prometheus/Alertmanager themselves are down.
#
# Probe path: /health/ready/ in quick mode — public, no auth, checks DB + Redis
# (core/health.py). /health/ only proves a gunicorn process answers.
#
# Standalone Health Checks need a Pro (or higher) zone plan; on Free set
# enable_uptime_checks = false and use an external monitor on the same URLs.
# Token also needs: Zone.Health Checks:Edit, Account.Notifications:Edit.

locals {
  uptime_hosts = var.enable_uptime_checks ? {
    root = var.domain
    api  = "api.${var.domain}"
  } : {}
}

resource "cloudflare_healthcheck" "uptime" {
  for_each = local.uptime_hosts

  zone_id        = var.zone_id
  name           = "uptime-${each.key}"
  description    = "External probe of https://${each.value}${var.uptime_path}"
  address        = each.value
  type           = "HTTPS"
  port           = 443
  method         = "GET"
  path           = var.uptime_path
  expected_codes = ["200"]
  # Body of a healthy quick readiness check (core/health.py readiness()).
  expected_body    = "\"status\": \"ready\""
  follow_redirects = false
  allow_insecure   = false

  header {
    header = "Host"
    values = [each.value]
  }

  check_regions         = ["WEU", "EEU", "WNAM"]
  interval              = 60
  timeout               = 5
  retries               = 2
  consecutive_fails     = 2
  consecutive_successes = 2
}

resource "cloudflare_notification_policy" "uptime" {
  count = var.enable_uptime_checks && length(var.uptime_alert_emails) > 0 ? 1 : 0

  account_id  = var.account_id
  name        = "yuzdanyuz uptime"
  description = "Origin health check went unhealthy / recovered"
  enabled     = true
  alert_type  = "health_check_status_notification"

  filters {
    health_check_id = [for hc in cloudflare_healthcheck.uptime : hc.id]
    status          = ["Unhealthy", "Healthy"]
  }

  dynamic "email_integration" {
    for_each = var.uptime_alert_emails
    content {
      id = email_integration.value
    }
  }
}

output "uptime_healthcheck_ids" {
  description = "Cloudflare health check ids (dashboard: Traffic > Health Checks)"
  value       = { for k, hc in cloudflare_healthcheck.uptime : k => hc.id }
}
