# Política de despliegues de Orion

> Documento interno de Kaleido Pagos (empresa ficticia, creada para este ejercicio). Última revisión: febrero de 2026. Responsable: equipo de Plataforma.

## Objetivo

Esta política define cuándo y cómo se publican cambios en producción. Busca que los cambios sean frecuentes y pequeños, porque un cambio chico es más fácil de revisar, de probar y de revertir que uno grande.

## Ventanas de despliegue

Los despliegues a producción de los servicios que participan en los cobros (Portal, Motor de cobros y Ledger) solo se hacen los **martes y jueves, entre las 10:00 y las 16:00** (hora de Buenos Aires). Fuera de esa ventana, solo se permiten correcciones urgentes asociadas a un incidente SEV1 o SEV2.

El Notificador y los servicios de reportes pueden desplegarse cualquier día hábil, entre las 9:00 y las 18:00.

Nunca se despliega los viernes después de las 13:00, ni el día anterior a un feriado.

## Período de congelamiento

Todos los años hay un congelamiento de cambios entre el **15 de diciembre y el 10 de enero**, porque es la época de mayor volumen de compras. Durante el congelamiento solo se permiten correcciones de incidentes SEV1, con la aprobación del gerente de Ingeniería. También se congela la semana del evento comercial de noviembre.

## Requisitos antes de desplegar

Un cambio solo puede desplegarse a producción si cumple todos estos requisitos:

1. Fue revisado y aprobado por al menos **dos personas** distintas de quien lo escribió. Para cambios en el Ledger, una de ellas debe ser del equipo de Contabilidad técnica.
2. Pasó todas las pruebas automáticas en la integración continua, incluidas las pruebas de contrato entre servicios.
3. Estuvo al menos **24 horas** en el entorno de preproducción (llamado **Ensayo**) sin errores nuevos.
4. Tiene un plan de reversión escrito en la descripción del cambio.
5. Los cambios de esquema de base de datos son compatibles hacia atrás: la versión anterior del servicio debe poder seguir funcionando con el esquema nuevo.

## Despliegue gradual (canary)

Los servicios de cobros se despliegan en tres etapas:

| Etapa | Porcentaje del tráfico | Duración mínima |
|---|---|---|
| 1 | 5 % | 20 minutos |
| 2 | 25 % | 30 minutos |
| 3 | 100 % | — |

Entre etapas, la herramienta de despliegue **Lanzadera** compara automáticamente la versión nueva con la anterior. Si la versión nueva tiene una tasa de errores más alta o una latencia peor, Lanzadera detiene el avance.

## Criterios de reversión

La reversión es automática si, en cualquier etapa, ocurre alguna de estas condiciones:

- La tasa de errores de la versión nueva supera el **2 %** durante **5 minutos** seguidos.
- La latencia del percentil 99 sube más de un **30 %** respecto de la versión anterior.
- Se dispara cualquier alerta SEV1 o SEV2 relacionada con el servicio desplegado.

Cualquier persona del equipo puede pedir una reversión manual sin tener que justificarla en el momento. Una reversión nunca se considera un fracaso: es el mecanismo de seguridad funcionando.

Después de una reversión automática, el mismo cambio no puede volver a desplegarse hasta que se identifique la causa y se agregue una prueba que la cubra.

## Banderas de funcionalidad

Las funcionalidades nuevas que cambian el comportamiento para los comercios se publican desactivadas, detrás de una bandera de funcionalidad. Se activan primero para un grupo de comercios piloto y luego para el resto. Las banderas que llevan más de **90 días** activadas para todos los comercios deben eliminarse del código.

## Registro

Cada despliegue queda registrado en el canal `#despliegues` con el servicio, la versión, la persona responsable y el resultado. Este registro se revisa durante los incidentes para identificar cambios recientes.
