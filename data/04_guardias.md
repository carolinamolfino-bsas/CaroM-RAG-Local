# Guía de guardias (on-call) de Orion

> Documento interno de Kaleido Pagos (empresa ficticia, creada para este ejercicio). Última revisión: abril de 2026. Responsable: equipo de Confiabilidad (SRE).

## Para qué existe la guardia

Orion procesa cobros las 24 horas, todos los días del año. La guardia asegura que siempre haya una persona capaz de responder a una alerta y de iniciar la atención de un incidente, aun fuera del horario laboral.

## Rotación

- Cada guardia dura **una semana** y empieza los **lunes a las 9:00**.
- Hay siempre dos personas: la **guardia primaria**, que recibe todas las alertas, y la **guardia secundaria**, que recibe la alerta si la primaria no la confirma.
- Si la guardia primaria no confirma una alerta en **5 minutos**, Sirena la envía a la secundaria. Si la secundaria tampoco la confirma en otros 5 minutos, la alerta escala al líder del equipo de Confiabilidad.
- Nadie hace más de una semana de guardia primaria por mes.
- Para participar de la rotación hace falta haber trabajado al menos **tres meses** en Kaleido y haber hecho una semana de guardia "en sombra" acompañando a otra persona.

Los cambios de turno entre compañeros están permitidos: basta con registrarlos en Sirena con al menos 48 horas de anticipación.

## Traspaso de guardia

El traspaso se hace los lunes a las 9:00 en una reunión de 15 minutos. Quien termina la guardia presenta:

- los incidentes de la semana y su estado;
- las alertas que se dispararon sin motivo real (falsas alarmas), para corregirlas;
- cualquier tarea pendiente o riesgo conocido para la semana que empieza.

Las falsas alarmas se registran como defecto de prioridad alta: una alerta que suena sin motivo entrena a las personas a ignorarla.

## Qué se espera de la persona de guardia

- Tener la computadora y una conexión a internet disponibles para responder dentro de los tiempos de cada severidad.
- Confirmar cada alerta en Sirena, aunque después resulte una falsa alarma.
- Seguir el procedimiento (runbook) asociado a la alerta. Si el procedimiento está desactualizado, se corrige después del incidente.
- Declarar el incidente ante la duda.

La persona de guardia **no** debe aplicar cambios grandes ni nuevas funcionalidades durante la noche. Su trabajo es estabilizar el servicio; la solución definitiva se planifica en horario laboral.

## Compensación

- Cada semana de guardia primaria se compensa con un **día libre**, que debe tomarse dentro de los 30 días siguientes.
- Además, cada alerta atendida entre las 22:00 y las 7:00 suma un adicional equivalente a **2 horas** de trabajo.
- Si una persona atiende alertas durante más de 3 horas en una misma noche, al día siguiente puede empezar a trabajar a las 13:00 sin necesidad de pedir permiso.

## Herramientas

- **Sirena:** recibe las alertas, las asigna a la guardia y registra las confirmaciones.
- **Canal `#incidentes`:** coordinación de los incidentes declarados.
- **Libro de procedimientos:** repositorio interno con un runbook por cada alerta.
- **Grafana:** paneles de métricas; el panel principal se llama "Salud de Orion".

## Cuidado de las personas

La guardia no debe convertirse en una carga permanente. El equipo de Confiabilidad revisa cada mes la cantidad de alertas nocturnas. Si una semana de guardia supera las **5 alertas nocturnas**, se abre una tarea obligatoria para reducir el ruido de las alertas involucradas.

Cualquier persona puede pedir salir temporalmente de la rotación por motivos personales o de salud, sin dar explicaciones, hablando con el líder del equipo.
