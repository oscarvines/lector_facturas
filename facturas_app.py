import os
import re
import pandas as pd
from google.cloud import documentai_v1 as documentai
from google.cloud import storage

# --- CONFIGURACIÓN ---
PROJECT_ID = "772723410003"
LOCATION = "us"
PROCESSOR_ID = "dff8117c158462cd"
BUCKET_NAME = "facturasclientes"
OUTPUT_DIR = "output_docai"

# Inicializar clientes
docai_client = documentai.DocumentProcessorServiceClient()
name = f"projects/{PROJECT_ID}/locations/{LOCATION}/processors/{PROCESSOR_ID}"
storage_client = storage.Client()
bucket = storage_client.bucket(BUCKET_NAME)

os.makedirs(OUTPUT_DIR, exist_ok=True)


# --- FUNCIONES AUXILIARES ---

def buscar_en_texto(texto, patron):
    match = re.search(patron, texto, re.IGNORECASE)
    return match.group(1).strip() if match else ""

def parse_float_es(valor):
    if not valor:
        return 0.0
    valor = valor.replace('.', '').replace(',', '.')
    try:
        return float(valor)
    except:
        return 0.0

def extraer_del_texto_libre(texto):
    base = buscar_en_texto(texto, r"base\s+imponible\s*[^\d]*(\d+[.,]\d+)")
    iva = buscar_en_texto(texto, r"(?:iva\s*\(?\d+%?\)?|i\s*v\s*a|i\.v\.a\.|i\.v\.a)\s*([0-9.,]+)")
    concepto = ""
    texto_upper = texto.upper()
    if "CONCEPTO" in texto_upper:
        split = texto_upper.split("CONCEPTO", 1)[-1]
        concepto = split.split("BASE IMPONIBLE")[0].strip() if "BASE IMPONIBLE" in split else split.strip()
    return base, iva, concepto

def procesar_factura(blob):
    content = blob.download_as_bytes()
    raw_document = documentai.RawDocument(content=content, mime_type="application/pdf")
    request = documentai.ProcessRequest(name=name, raw_document=raw_document)
    result = docai_client.process_document(request=request)
    doc = result.document

    supplier = ""
    cif_supplier = ""
    customer = ""
    cif_customer = ""
    invoice_date = ""
    invoice_id = ""
    base_total = 0.0
    iva_total = 0.0
    total_global = ""
    concepto_unico = ""

    for e in doc.entities:
        if e.type_ == "supplier_name":
            supplier = e.mention_text
        elif e.type_ == "supplier_tax_id":
            cif_supplier = e.mention_text
        elif e.type_ == "customer_name":
            customer = e.mention_text
        elif e.type_ == "customer_tax_id":
            cif_customer = e.mention_text
        elif e.type_ == "invoice_date":
            invoice_date = e.mention_text
        elif e.type_ == "invoice_id":
            invoice_id = e.mention_text
        elif e.type_ == "total_amount":
            total_global = e.mention_text
        elif e.type_ == "vat":
            for prop in e.properties:
                if prop.type_ == "vat/amount":
                    base_total += parse_float_es(prop.mention_text)
                elif prop.type_ == "vat/tax_amount":
                    iva_total += parse_float_es(prop.mention_text)

    descripciones = []
    for e in doc.entities:
        if e.type_ == "line_item":
            for p in e.properties:
                if p.type_ == "line_item/description":
                    descripciones.append(p.mention_text)

    concepto_unico = " | ".join(descripciones).strip()

    if not base_total or not iva_total:
        base_fbk, iva_fbk, concepto_fbk = extraer_del_texto_libre(doc.text)
        if not base_total and base_fbk:
            base_total = parse_float_es(base_fbk)
        if not iva_total and iva_fbk:
            iva_total = parse_float_es(iva_fbk)
        if not concepto_unico and concepto_fbk:
            concepto_unico = concepto_fbk

    fila = {
        "Archivo": blob.name,
        "Proveedor": supplier,
        "CIF_Proveedor": cif_supplier,
        "Cliente": customer,
        "CIF_Cliente": cif_customer,
        "Fecha": invoice_date,
        "Nº Factura": invoice_id,
        "Base Imponible": f"{base_total:.2f}" if base_total else "",
        "IVA": f"{iva_total:.2f}" if iva_total else "",
        "Total": total_global,
        "Concepto": concepto_unico
    }

    return [fila]

def guardar_excel(cliente, proyecto, filas):
    nombre = f"{cliente}_{proyecto}.xlsx"
    ruta = os.path.join(OUTPUT_DIR, nombre)

    df_nuevo = pd.DataFrame(filas)
    if os.path.exists(ruta):
        df_existente = pd.read_excel(ruta)
        df = pd.concat([df_existente, df_nuevo], ignore_index=True)
    else:
        df = df_nuevo

    df.to_excel(ruta, index=False)
    print(f"✅ Guardado/actualizado: {ruta}")


# --- INTERFAZ INTERACTIVA ---

def seleccionar_opcion(lista, titulo):
    print(f"\n📂 {titulo}")
    for i, item in enumerate(lista, 1):
        print(f"{i}. {item}")
    while True:
        try:
            idx = int(input("Selecciona el número: ")) - 1
            if 0 <= idx < len(lista):
                return lista[idx]
        except:
            pass
        print("❌ Opción no válida. Intenta de nuevo.")

def main_interactivo():
    print("🧾 Procesador de facturas con Document AI")

    # Obtener estructura cliente/proyecto
    blobs = bucket.list_blobs()
    clientes = set()
    proyectos = {}

    for blob in blobs:
        partes = blob.name.split("/")
        if len(partes) >= 2 and blob.name.endswith(".pdf"):
            cliente, proyecto = partes[0], partes[1]
            clientes.add(cliente)
            if cliente not in proyectos:
                proyectos[cliente] = set()
            proyectos[cliente].add(proyecto)

    clientes = sorted(clientes)
    proyectos = {k: sorted(list(v)) for k, v in proyectos.items()}

    # Selección interactiva
    cliente = seleccionar_opcion(clientes, "¿Qué cliente quieres procesar?")
    proyecto = seleccionar_opcion(proyectos[cliente], f"¿Qué proyecto de {cliente} quieres procesar?")

    print(f"\n🔍 Buscando facturas en {cliente}/{proyecto}...\n")
    blobs = bucket.list_blobs()

    encontrados = False
    for blob in blobs:
        if not blob.name.lower().endswith(".pdf"):
            continue
        if not blob.name.startswith(f"{cliente}/{proyecto}/"):
            continue

        encontrados = True
        print(f"📄 Procesando {blob.name}...")
        fila = procesar_factura(blob)
        guardar_excel(cliente, proyecto, fila)

    if not encontrados:
        print("⚠️ No se encontraron PDFs en esa ruta del bucket.")
    else:
        print("✅ Proceso finalizado.")

if __name__ == "__main__":
    main_interactivo()

