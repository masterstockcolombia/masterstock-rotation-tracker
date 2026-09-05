# Heartbeat

Este archivo existe solo para disparar el cron diario de scraping.
Un cron externo (cron-job.org) lo toca una vez al dia via la API de
contents de GitHub -- ese push es el UNICO evento que confirmamos que
dispara jobs reales en este repo (ver commits d54f454/1225cc9/7225aaa/
85a6560/a456fce para la evidencia completa de por que workflow_dispatch,
schedule y repository_dispatch no funcionan de forma confiable aca).

No editar a mano salvo para pruebas puntuales.

Ultimo toque: 2026-09-05T01:50:00Z (prueba manual de diagnostico)
