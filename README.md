# 🧾 Lector de Facturas con Document AI

Este repositorio contiene una herramienta para procesar facturas PDF alojadas en un bucket de Google Cloud Storage, utilizando **Google Document AI**. Extrae automáticamente datos como proveedor, CIF, base imponible, IVA, total y concepto, y los organiza en un archivo Excel por cliente y proyecto.

---

## 📁 Estructura del proyecto

```
lectorfacturas/
├── facturas_app.py             # Interfaz interactiva por consola (modo guiado)
├── process_with_docai.py       # Procesamiento por lotes con argumentos --cliente y --proyecto
├── output_docai/               # Carpeta donde se generan los Excels resultantes
├── requirements.txt            # Librerías necesarias
├── .gitignore                  # Archivos y carpetas excluidos del repositorio
└── README.md                   # Este archivo
```

---

## ⚙️ Requisitos previos

* Tener un bucket GCS con facturas organizadas en carpetas:

  ```
  BUCKET/
  ├── Cliente1/
  │   └── ProyectoA/
  │       ├── factura1.pdf
  │       └── factura2.pdf
  └── Cliente2/
      └── ProyectoB/
          └── factura3.pdf
  ```
* Haber creado un **Invoice Processor** en Google Document AI.
* Tener configuradas las credenciales de autenticación (`gcloud auth application-default login`).

---

## 🚀 Ejecución

### A. Modo interactivo (guiado)

```bash
python facturas_app.py
```

Te preguntará qué cliente y proyecto quieres procesar y guardará automáticamente el Excel.

### B. Modo por lotes con argumentos

```bash
python process_with_docai.py --cliente Cliente1 --proyecto ProyectoA
```

Procesa solo los PDFs de ese subdirectorio.

---

## 📦 Salida

Los archivos generados se guardan como:

```
output_docai/Cliente1_ProyectoA.xlsx
```

Cada fila representa una factura procesada. Las columnas incluyen:

* Proveedor
* CIF Proveedor
* Cliente
* CIF Cliente
* Fecha
* Nº Factura
* Base Imponible
* IVA
* Total
* Concepto

---

## 🧪 Recomendaciones

* Puedes ejecutar ambos scripts de forma independiente.
* Si una factura no incluye correctamente `vat` o `net_amount`, se aplica OCR para extraer los datos desde el texto plano.

---

## ✅ Limpieza realizada

* Eliminadas versiones antiguas (`output_docai_v3` y scripts `v3`).
* Unificada salida en carpeta `output_docai/`.
* Proyecto preparado para despliegue, testing o ampliaciones.

---

## ✉️ Contacto

Desarrollado por [@oscarvines](https://github.com/oscarvines)

---

¿Quieres colaborar o mejorar este sistema? ¡Bienvenidas las PRs!
