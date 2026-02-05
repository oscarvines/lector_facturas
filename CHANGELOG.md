# Historial de Cambios - Lector de Facturas

## [v3.1.0] - 2026-02-05
### Añadido
- **Validación Dual de Impuestos:** El sistema ahora no solo comprueba que la suma (Base + IVA) coincida con el Total, sino que verifica si el IVA aplicado es exactamente el **21%**.
- **Alertas Visuales:** Se ha implementado un código de colores en la tabla de la App; cualquier fila que no sea "CORRECTA" se resalta en rojo.
- **Mensajes de Error Específicos:** La columna de validación ahora distingue entre "ERROR SUMA" y "REVISAR (No es 21%)".

### Modificado
- **Ajuste de Filtros Preventivos:** Se han eliminado los nombres de bancos ("Cajamar", "CaixaBank") de los filtros automáticos para evitar falsos positivos en facturas legítimas que incluyan datos bancarios.
- **Lógica de Ahorro:** Se mantienen los patrones de texto específicos como "justificante de pago" o "remesa" para evitar llamadas innecesarias a la API de Google.

## [v3.0.0] - 2026-02-05
### Añadido
- **Procesamiento Multi-página:** Capacidad para separar y leer cada página de un PDF como una factura independiente.
- **Integración Document AI V3:** Cambio al procesador especializado de Google Cloud.
