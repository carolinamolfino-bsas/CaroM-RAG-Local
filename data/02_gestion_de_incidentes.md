# Gestión de incidentes en Orion

> Documento interno de Kaleido Pagos (empresa ficticia, creada para este ejercicio). Última revisión: mayo de 2026. Responsable: equipo de Confiabilidad (SRE).

## ¿Qué es un incidente?

Un incidente es cualquier evento no planificado que degrada o interrumpe el servicio que Orion presta a los comercios, o que pone en riesgo la integridad de los datos financieros. Un error aislado que afecta a una sola solicitud y se recupera solo no es un incidente; se registra como defecto.

Ante la duda, se declara el incidente. Declarar un incidente que después resulta menor no tiene ninguna consecuencia negativa; no declarar uno real sí la tiene.

## Niveles de severidad

### SEV1: crítico

Un SEV1 es una caída total de los cobros, una pérdida o corrupción de datos del Ledger, o una exposición de datos de tarjeta. También es SEV1 cualquier situación en la que más del 25 % de los cobros falle durante más de 5 minutos.

- Tiempo máximo de respuesta de la guardia: **10 minutos** desde la alerta.
- Se convoca de inmediato a un Comandante de incidente, que coordina y no ejecuta cambios.
- Actualización a los comercios en la página de estado cada **20 minutos**.
- Se informa a la gerencia general dentro de la primera hora.

### SEV2: alto

Un SEV2 es una degradación importante: entre el 5 % y el 25 % de los cobros falla, la latencia del percentil 99 supera los 3 segundos durante más de 10 minutos, o una funcionalidad completa (por ejemplo, las transferencias inmediatas) deja de funcionar.

- Tiempo máximo de respuesta: **20 minutos**.
- Actualización en la página de estado cada **45 minutos**.

### SEV3: medio

Un SEV3 afecta a una parte acotada de los comercios o a una función secundaria, como los reportes o el Notificador, sin impedir los cobros.

- Tiempo máximo de respuesta: **2 horas** dentro del horario laboral.
- No requiere comunicación pública salvo que dure más de un día.

### SEV4: bajo

Un SEV4 es un problema menor sin impacto visible para los comercios: por ejemplo, un panel de Grafana que no carga o una alerta mal configurada. Se atiende dentro de los **3 días hábiles** siguientes.

## Roles durante un incidente

- **Comandante de incidente:** coordina, decide prioridades y define cuándo se cierra el incidente. No escribe código ni ejecuta comandos durante el incidente.
- **Responsable técnico:** investiga la causa y aplica las mitigaciones. En un SEV1 puede pedir ayuda a cualquier equipo, que debe responder en menos de 15 minutos.
- **Responsable de comunicación:** redacta las actualizaciones para la página de estado y para el canal interno `#incidentes`.
- **Escriba:** registra en orden cronológico todas las decisiones y acciones. Este registro es la base del postmortem.

En un SEV3 o SEV4, una misma persona puede cumplir todos los roles.

## Flujo de atención

1. **Detección:** la alerta llega por Sirena o un comercio reporta el problema a soporte.
2. **Declaración:** la persona de guardia declara el incidente en `#incidentes` con el comando `/incidente nuevo`, indicando la severidad inicial.
3. **Mitigación:** la prioridad es restablecer el servicio, no encontrar la causa raíz. Si el último despliegue es sospechoso, se revierte primero y se investiga después.
4. **Resolución:** el Comandante cierra el incidente cuando las métricas vuelven a la normalidad durante al menos 30 minutos.
5. **Postmortem:** se escribe y se revisa después del cierre.

La severidad puede subir o bajar durante el incidente; cada cambio se anuncia en el canal.

## Postmortems

Todo SEV1 y SEV2 requiere un postmortem escrito. Para un SEV3 es opcional y lo decide el líder del equipo afectado.

- El borrador debe estar listo dentro de los **5 días hábiles** posteriores al cierre.
- Se revisa en la reunión semanal de confiabilidad de los miércoles a las 15:00.
- El formato es "sin culpables": se analizan sistemas y procesos, nunca personas.
- Cada postmortem termina con acciones concretas, cada una con un responsable y una fecha. Las acciones de un SEV1 deben completarse en un máximo de 30 días.

El postmortem incluye: resumen, impacto (cantidad de cobros afectados y duración), línea de tiempo, causa raíz, qué funcionó bien, qué no funcionó y acciones.

## Métricas del proceso

El equipo de Confiabilidad publica cada mes dos indicadores: el tiempo medio de detección (MTTD) y el tiempo medio de resolución (MTTR) de los incidentes SEV1 y SEV2. El objetivo para 2026 es un MTTR menor a 45 minutos para los SEV1.
