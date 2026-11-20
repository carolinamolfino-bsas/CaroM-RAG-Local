# Arquitectura de la plataforma Orion

> Documento interno de Kaleido Pagos (empresa ficticia, creada para este ejercicio). Última revisión: marzo de 2026. Responsable: equipo de Plataforma.

## Propósito de Orion

Orion es la plataforma que procesa los cobros con tarjeta y las transferencias inmediatas de Kaleido Pagos. Atiende a unos 1.800 comercios y procesa, en un día normal, alrededor de 420.000 transacciones. El pico histórico fue de 61 transacciones por segundo durante el evento comercial de noviembre de 2025.

La plataforma se diseñó con tres principios: ninguna transacción se pierde, cada operación se puede auditar de punta a punta y ningún componente individual puede dejar fuera de servicio a todo el sistema.

## Componentes principales

### Portal (gateway de entrada)

El Portal es el único punto de entrada para los comercios. Está escrito en Go y corre detrás de un balanceador HAProxy. Sus responsabilidades son autenticar a cada comercio con claves rotadas cada 90 días, aplicar límites de tasa (por defecto 30 solicitudes por segundo por comercio) y enrutar cada solicitud al servicio interno correspondiente.

El Portal no guarda estado propio: si una instancia falla, el balanceador deja de enviarle tráfico en menos de 5 segundos. En producción siempre hay como mínimo cuatro instancias activas, repartidas en dos zonas de disponibilidad.

### Motor de cobros

El Motor de cobros es el corazón de Orion. Está escrito en Java 21 con Spring Boot y es el único servicio autorizado a comunicarse con las redes de tarjetas. Cada cobro pasa por cuatro estados: `recibido`, `autorizado`, `capturado` y `liquidado`. Un cobro nunca puede retroceder de estado; si algo falla, se crea una operación de reverso nueva.

El Motor aplica un tiempo máximo de 8 segundos para obtener respuesta de la red de tarjetas. Si se supera, el cobro queda en estado `pendiente_revision` y lo resuelve un proceso de conciliación que corre cada 15 minutos.

### Libro contable (Ledger)

El Ledger registra cada movimiento de dinero con el método de partida doble: todo débito tiene su crédito equivalente. Usa PostgreSQL 15 con una instancia primaria y dos réplicas de lectura. Las réplicas se usan para reportes y nunca para escrituras.

Los registros del Ledger son inmutables. Para corregir un error se agrega un asiento de ajuste; nunca se modifica ni se borra un asiento existente. Se hace una copia de seguridad completa cada 24 horas y copias incrementales cada 15 minutos. Las copias se conservan durante 7 años, por exigencia regulatoria.

### Cola de eventos

Los servicios se comunican de forma asíncrona a través de RabbitMQ. Cada evento de negocio (por ejemplo, `cobro.capturado`) se publica una sola vez y lo consumen los servicios interesados. Los mensajes que fallan cinco veces seguidas pasan a una cola de mensajes muertos, que el equipo de guardia revisa todos los días hábiles a las 11:00.

### Caché

Redis se usa como caché de la configuración de los comercios y de los tipos de cambio. La configuración de un comercio se guarda en caché durante 10 minutos; los tipos de cambio, durante 60 segundos. Redis no guarda ningún dato que no pueda reconstruirse: si se pierde por completo, Orion sigue funcionando, aunque más lento.

### Notificador

El Notificador envía a los comercios los avisos de cada cobro mediante webhooks. Si el servidor del comercio no responde, reintenta con espera exponencial durante un máximo de 24 horas. Está escrito en Python y es el único componente que puede quedar atrasado sin afectar a los cobros.

## Infraestructura

Orion corre en dos regiones de nube: la región principal, llamada internamente **Austral**, y la región de contingencia, llamada **Boreal**. Austral atiende todo el tráfico en condiciones normales. Boreal mantiene una copia actualizada de los datos con un retraso máximo de 30 segundos y puede tomar el tráfico en caso de desastre.

El objetivo de tiempo de recuperación (RTO) ante la pérdida total de Austral es de 1 hora, y el objetivo de punto de recuperación (RPO) es de 1 minuto. La conmutación a Boreal se ensaya dos veces por año, en abril y en octubre.

Los servicios se empaquetan en contenedores y se orquestan con Nomad. La configuración de la infraestructura se gestiona como código con Terraform, y ningún cambio de infraestructura se aplica a mano en producción.

## Observabilidad

Toda la plataforma envía métricas a Prometheus y los registros (logs) a un clúster de Loki. Los paneles se arman en Grafana. Cada servicio publica, como mínimo, tres métricas obligatorias: tasa de solicitudes, tasa de errores y latencia en el percentil 99.

Las alertas se envían a **Sirena**, la herramienta interna de avisos de guardia. Una alerta que no tiene un procedimiento (runbook) asociado no puede pasar a producción.

## Seguridad

Los datos de tarjeta nunca se guardan en texto plano: el Motor de cobros los reemplaza por un token apenas los recibe, y solo el servicio de bóveda puede volver a obtener el número original. El acceso a producción requiere doble factor y queda registrado. Ningún desarrollador tiene acceso permanente a la base de datos del Ledger; los accesos se piden por 4 horas como máximo y requieren la aprobación de un líder técnico.
