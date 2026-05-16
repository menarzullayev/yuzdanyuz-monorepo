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
