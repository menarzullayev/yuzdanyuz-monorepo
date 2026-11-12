variable "cloudflare_api_token" {
  description = "Cloudflare API token (Zone.DNS, Zone.WAF, Zone.PageRules, Zone.Settings edit perms)"
  type        = string
  sensitive   = true
}

variable "zone_id" {
  description = "Cloudflare zone ID for the domain"
  type        = string
}

variable "domain" {
  description = "Bare domain (e.g. yuzdanyuz.uz) — without scheme or trailing slash"
  type        = string
  default     = "yuzdanyuz.uz"
}

variable "origin_ip" {
  description = "Public IPv4 of the K8s ingress LoadBalancer (or LB hostname via separate CNAME)"
  type        = string
}

# ── Uptime probes (uptime.tf) ─────────────────────────────────────────────────

variable "enable_uptime_checks" {
  description = "Create Cloudflare health checks for the public hosts (needs a Pro+ zone plan)"
  type        = bool
  default     = true
}

variable "uptime_path" {
  description = "Path probed on every public host"
  type        = string
  default     = "/health/ready/"
}

variable "account_id" {
  description = "Cloudflare account ID (for the uptime notification policy)"
  type        = string
  default     = ""
}

variable "uptime_alert_emails" {
  description = "Emails notified when a probe goes unhealthy or recovers; empty = no notification policy"
  type        = list(string)
  default     = []
}
