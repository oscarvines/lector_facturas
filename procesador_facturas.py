import io
import re
from google.cloud import documentai_v1 as documentai
from PyPDF2 import PdfReader, PdfWriter


# -------- INIT CLIENT --------

def init_docai_client(creds):
    return documentai.DocumentProcessorServiceClient(credentials=creds)


# -------- UTILIDADES --------

def parse_amount(valor: str) -> float:
    if not valor:
        return 0.0
    s = re.sub(r"[^\d,.\-]", "", str(valor))
    if not s:
        return 0.0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return 0.0


def es_justificante_local(texto):
    patrones = [
        r"detalle de orden", r"remesa", r"cuenta ordenante", r"cuenta beneficiario",
        r"justificante de pago", r"transferencia realizada", r"ejecutada", r"abono"
    ]
    return any(re.search(p, texto.lower()) for p in patrones)


# -------- DOCUMENT AI --------

def llamar_a_document_ai(client, processor_name, pdf_bytes):
    raw_doc = documentai.RawDocument(content=pdf_bytes, mime_type="application/pdf")
    req = documentai.ProcessRequest(name=processor_name, raw_document=raw_doc)
    res = client.process_document(request=req)
    return res.document


# -------- PROCESAMIENTO PRINCIPAL --------

def procesar_archivo(file_bytes, filename, client, processor_name):
    pdf_reader = PdfReader(io.BytesIO(file_bytes))

    facturas = []
    lineas = []

    for i, page in enumerate(pdf_reader.pages):

        texto_local = page.extract_text() or ""

        # Filtro de justificantes
        if es_justificante_local(texto_local):
            continue

        writer = PdfWriter()
        writer.add_page(page)

        with io.BytesIO() as buf:
            writer.write(buf)
            doc = llamar_a_document_ai(client, processor_name, buf.getvalue())

            # -------- ID FACTURA --------
            id_factura = f"{filename}_{i+1}"

            # -------- FACTURA --------
            data_factura = {
                "id_factura": id_factura,
                "archivo": filename,
                "proveedor": "",
                "cif_proveedor": "",
                "cliente": "",
                "cif_cliente": "",
                "fecha_factura": "",
                "num_factura": "",
                "base": 0.0,
                "iva": 0.0,
                "total": 0.0,
                "validacion": "",
                "total_aceptado": 0.0
            }

            base_c, iva_c, total_c = [], [], []
            line_items = []

            # -------- PARSEO ENTIDADES --------

            for e in doc.entities:
                t = e.type_
                text = e.mention_text or ""

                # CABECERA
                if t == "supplier_name":
                    data_factura["proveedor"] = text.replace("\n", " ")

                elif t == "supplier_tax_id":
                    data_factura["cif_proveedor"] = text

                elif t == "customer_name":
                    data_factura["cliente"] = text.replace("\n", " ")

                elif t == "customer_tax_id":
                    data_factura["cif_cliente"] = text

                elif t == "invoice_date":
                    data_factura["fecha_factura"] = text

                elif t == "invoice_id":
                    data_factura["num_factura"] = text

                elif t == "total_amount":
                    total_c.append(text)

                elif t == "net_amount":
                    base_c.append(text)

                elif t == "total_tax_amount":
                    iva_c.append(text)

                # -------- LINE ITEMS --------

                elif t == "line_item":

                    item = {
                        "descripcion": "",
                        "cantidad": 0.0,
                        "precio_unitario": 0.0,
                        "importe": 0.0,
                        "unidad": "",
                        "confidence": e.confidence
                    }

                    for prop in e.properties:
                        ptype = prop.type_
                        ptext = prop.mention_text or ""

                        if ptype == "line_item/description":
                            item["descripcion"] = ptext

                        elif ptype == "line_item/quantity":
                            item["cantidad"] = parse_amount(ptext)

                        elif ptype == "line_item/unit_price":
                            item["precio_unitario"] = parse_amount(ptext)

                        elif ptype == "line_item/amount":
                            item["importe"] = parse_amount(ptext)

                        elif ptype == "line_item/unit":
                            item["unidad"] = ptext

                    line_items.append(item)

            # -------- CALCULO FACTURA --------

            data_factura["base"] = max([parse_amount(v) for v in base_c] or [0.0])
            data_factura["iva"] = max([parse_amount(v) for v in iva_c] or [0.0])
            data_factura["total"] = max([parse_amount(v) for v in total_c] or [0.0])

            suma = round(data_factura["base"] + data_factura["iva"], 2)

            data_factura["validacion"] = (
                "CORRECTA"
                if data_factura["total"] > 0 and abs(suma - data_factura["total"]) < 0.05
                else "REVISAR"
            )

            facturas.append(data_factura)

            # -------- FALLBACK SI NO HAY LINEAS --------

            if not line_items:
                line_items = [{
                    "descripcion": "TOTAL FACTURA",
                    "cantidad": 1,
                    "precio_unitario": data_factura["base"],
                    "importe": data_factura["base"],
                    "unidad": "",
                    "confidence": 1.0
                }]

            # -------- CREAR TABLA LINEAS --------

            for idx, item in enumerate(line_items):
                lineas.append({
                    "id_linea": f"{id_factura}_{idx}",
                    "id_factura": id_factura,
                    "proveedor": data_factura["proveedor"],
                    "cliente": data_factura["cliente"],
                    "descripcion": item["descripcion"],
                    "cantidad": item["cantidad"],
                    "precio_unitario": item["precio_unitario"],
                    "importe": item["importe"],
                    "unidad": item["unidad"],
                    "aceptada": True,
                    "confidence": item["confidence"]
                })

    return facturas, lineas