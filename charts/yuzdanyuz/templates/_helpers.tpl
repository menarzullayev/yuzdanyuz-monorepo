{{/*
Expand the name of the chart.
*/}}
{{- define "yuzdanyuz.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "yuzdanyuz.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
Common labels — applied to every resource.
*/}}
{{- define "yuzdanyuz.labels" -}}
app.kubernetes.io/name: {{ include "yuzdanyuz.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/*
Selector labels — must NOT change between releases (immutable).
*/}}
{{- define "yuzdanyuz.selectorLabels" -}}
app.kubernetes.io/name: {{ include "yuzdanyuz.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Image reference — combines registry + repository + tag.
*/}}
{{- define "yuzdanyuz.image" -}}
{{- $registry := .Values.global.imageRegistry | default "" -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry .Values.image.repository .Values.image.tag -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}
{{- end -}}

{{/*
Nginx image reference.
*/}}
{{- define "yuzdanyuz.nginxImage" -}}
{{- $registry := .Values.global.imageRegistry | default "" -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry .Values.nginx.image.repository .Values.nginx.image.tag -}}
{{- else -}}
{{- printf "%s:%s" .Values.nginx.image.repository .Values.nginx.image.tag -}}
{{- end -}}
{{- end -}}

{{/*
Render env vars from .Values.env (ConfigMap-style) + secret refs.
*/}}
{{- define "yuzdanyuz.envFrom" -}}
- configMapRef:
    name: {{ include "yuzdanyuz.fullname" . }}-env
- secretRef:
    name: {{ .Values.secretRef.name }}
{{- end -}}

{{/*
Env overrides for pods that must reach Postgres directly (migrate Job), bypassing
PgBouncer: DB_HOST/DB_PORT of the server, its sslmode, and no pooler mode.
*/}}
{{- define "yuzdanyuz.directDbEnv" -}}
{{- $pg := .Values.externalServices.postgres -}}
- name: DB_HOST
  value: {{ $pg.host | quote }}
- name: DB_PORT
  value: {{ $pg.port | quote }}
- name: DB_POOLER
  value: ""
- name: DB_SSLMODE
  value: {{ $pg.sslmode | quote }}
{{- with $pg.sslrootcert }}
- name: DB_SSLROOTCERT
  value: {{ . | quote }}
{{- end }}
{{- end -}}

{{/*
Fail rendering when a backend workload sets `command` (it would replace the image
ENTRYPOINT). Use `args` instead.
*/}}
{{- define "yuzdanyuz.assertNoCommand" -}}
{{- range $k := list "web" "channels" "celeryWorker" "celeryWorkerCritical" "celeryWorkerAI" "celeryWorkerRealtime" "celeryWorkerBatch" "celeryBeat" "migrate" -}}
{{- if hasKey (index $.Values $k) "command" -}}
{{- fail (printf "%s.command is set: it replaces the image ENTRYPOINT (tini + /entrypoint.sh). Use %s.args instead." $k $k) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Probe HTTP headers: kubelet otherwise sends Host=<pod IP>.
*/}}
{{- define "yuzdanyuz.probeHeaders" -}}
httpHeaders:
  - name: Host
    value: {{ .Values.probes.host | quote }}
{{- end -}}

{{/*
POD_IP from the downward API; prod.py adds it to ALLOWED_HOSTS so Prometheus can
scrape /metrics by pod IP.
*/}}
{{- define "yuzdanyuz.podIPEnv" -}}
- name: POD_IP
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
{{- end -}}

{{/*
ALLOWED_HOSTS: global.domain plus every ingress host.
*/}}
{{- define "yuzdanyuz.allowedHosts" -}}
{{- $hosts := list .Values.global.domain -}}
{{- range .Values.ingress.hosts -}}
{{- $hosts = append $hosts .host -}}
{{- end -}}
{{- $hosts | uniq | join "," -}}
{{- end -}}

{{/*
Backup image reference (docker/Dockerfile.backup).
*/}}
{{- define "yuzdanyuz.backupImage" -}}
{{- $registry := .Values.global.imageRegistry | default "" -}}
{{- $tag := .Values.backup.image.tag | default .Values.image.tag -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry .Values.backup.image.repository $tag -}}
{{- else -}}
{{- printf "%s:%s" .Values.backup.image.repository $tag -}}
{{- end -}}
{{- end -}}
