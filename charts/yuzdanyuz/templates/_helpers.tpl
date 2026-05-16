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
