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
Non-secret settings (ConfigMap). Secrets are never imported wholesale: see secretEnv.
*/}}
{{- define "yuzdanyuz.envFrom" -}}
- configMapRef:
    name: {{ include "yuzdanyuz.fullname" . }}-env
{{- end -}}

{{/*
One secretKeyRef per key (audit B15: least privilege). Call with
  (dict "root" $ "keys" (list ...) "as" (dict "ENV_NAME" "SECRET_KEY_NAME"))
`as` maps an env var to a differently named key (e.g. DB_PASSWORD <- DB_MIGRATE_PASSWORD).
Keys in .Values.secretRef.required must exist; the rest are optional.
*/}}
{{- define "yuzdanyuz.secretEnv" -}}
{{- $root := .root -}}
{{- $seen := dict -}}
{{- range $env, $key := (.as | default dict) }}
{{- $_ := set $seen $env true }}
- name: {{ $env }}
  valueFrom:
    secretKeyRef:
      name: {{ $root.Values.secretRef.name }}
      key: {{ $key }}
      optional: {{ not (has $key $root.Values.secretRef.required) }}
{{- end }}
{{- range $key := (.keys | uniq) }}
{{- if not (hasKey $seen $key) }}
- name: {{ $key }}
  valueFrom:
    secretKeyRef:
      name: {{ $root.Values.secretRef.name }}
      key: {{ $key }}
      optional: {{ not (has $key $root.Values.secretRef.required) }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
Secret env for a backend workload: common keys + the workload's own list.
Call with (dict "root" $ "workload" "web").
*/}}
{{- define "yuzdanyuz.backendSecretEnv" -}}
{{- $keys := concat .root.Values.secretRef.common (index .root.Values.secretRef .workload) -}}
{{- include "yuzdanyuz.secretEnv" (dict "root" .root "keys" $keys) -}}
{{- end -}}

{{/*
Writable scratch space for a read-only root filesystem.
*/}}
{{- define "yuzdanyuz.tmpMount" -}}
- name: tmp
  mountPath: /tmp
{{- end -}}
{{- define "yuzdanyuz.tmpVolume" -}}
- name: tmp
  emptyDir:
    sizeLimit: 1Gi
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
