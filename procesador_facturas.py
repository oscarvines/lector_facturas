import io
import re
from google.cloud import documentai_v1 as documentai
from PyPDF2 import PdfReader, PdfWriter

# -------- INIT CLIENT --------

def init_docai_client(creds):
    """Initialize the Document AI client with explicit credentials.

    Parameters
    ----------
    creds: google.oauth2.credentials.Credentials
        The Google Cloud credentials object loaded from service
        account information.  This must include the appropriate
        permissions to access the Document AI processor.

    Returns
    -------
    documentai.DocumentProcessorServiceClient
        A client instance used to call the processor.
    """
    return documentai.DocumentProcessorServiceClient(credentials=creds)


# -------- UTILITIES --------

def parse_amount(valor: str) -> float:
    """Normalize a numeric string into a float.

    This helper strips all non-digit/decimal characters, handles
    European and US decimal formats, and returns a rounded float.  It
    is used extensively when parsing numeric values from the OCR
    output.

    Parameters
    ----------
    valor: str
        A string containing a number possibly mixed with currency
        symbols, commas, or periods.

    Returns
    -------
    float
        The parsed numeric value or 0.0 if parsing fails.
    """
    if not valor:
        return 0.0
    # Strip all characters except digits, commas, periods and minus
    s = re.sub(r"[^\d,\.\-]", "", str(valor))
    if not s:
        return 0.0
    # Handle both comma and period as decimal separators
    if "," in s and "." in s:
        # Determine which separator appears last and treat it as the
        # decimal separator. Remove thousands separators accordingly.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # Only comma present; treat it as decimal separator
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return 0.0


def es_justificante_local(texto):
    """Detect whether a page likely represents a bank receipt.

    This function checks for keywords that typically appear on bank
    receipts or transfer confirmations.  If any pattern matches,
    the page is skipped from invoice processing.

    Parameters
    ----------
    texto: str
        The extracted plaintext of a PDF page.

    Returns
    -------
    bool
        True if the text matches any pattern indicating a bank receipt.
    """
    patrones = [
        r"detalle de orden",
        r"remesa",
        r"cuenta ordenante",
        r"cuenta beneficiario",
        r"justificante de pago",
        r"transferencia realizada",
        r"ejecutada",
        r"abono",
    ]
    texto_lower = texto.lower()
    return any(re.search(p, texto_lower) for p in patrones)


# -------- DOCUMENT AI --------

def llamar_a_document_ai(client, processor_name: str, pdf_bytes: bytes):
    """Invoke Document AI on a single-page PDF.

    Parameters
    ----------
    client: documentai.DocumentProcessorServiceClient
        The initialized Document AI client.
    processor_name: str
        Full resource name of the processor version to call.
    pdf_bytes: bytes
        Raw bytes of the single-page PDF.

    Returns
    -------
    documentai.Document
        The processed document with entities and text.
    """
    raw_doc = documentai.RawDocument(content=pdf_bytes, mime_type="application/pdf")
    req = documentai.ProcessRequest(name=processor_name, raw_document=raw_doc)
    res = client.process_document(request=req)
    return res.document


# -------- MAIN PROCESSING --------

def procesar_archivo(file_bytes: bytes, filename: str, client: documentai.DocumentProcessorServiceClient, processor_name: str):
    """Process an uploaded PDF and extract invoice-level and line-item data.

    This function handles multi-page PDFs by iterating through pages
    individually.  It applies a justification filter to skip bank
    receipts, invokes Document AI, and constructs two structures:
    ``facturas`` — one entry per invoice (header) — and ``lineas`` —
    detailed line items.  A fallback line is created when no line
    items are detected.

    Parameters
    ----------
    file_bytes: bytes
        Raw bytes of the uploaded PDF file.
    filename: str
        The name of the uploaded file.
    client: documentai.DocumentProcessorServiceClient
        An initialized Document AI client.
    processor_name: str
        Full resource name of the processor version to call.

    Returns
    -------
    (list[dict], list[dict])
        A tuple containing a list of invoice headers and a list of
        detailed line items.
    """
    pdf_reader = PdfReader(io.BytesIO(file_bytes))

    facturas: list[dict] = []
    lineas: list[dict] = []

    for i, page in enumerate(pdf_reader.pages):
        # Extract raw text for heuristic filtering
        texto_local = page.extract_text() or ""
        # Skip bank receipts or irrelevant pages
        if es_justificante_local(texto_local):
            continue
        # Assemble single-page PDF for processing
        writer = PdfWriter()
        writer.add_page(page)
        with io.BytesIO() as buf:
            writer.write(buf)
            doc = llamar_a_document_ai(client, processor_name, buf.getvalue())

        # Compose internal invoice identifier (filename + page index)
        id_factura = f"{filename}_{i+1}"

        # Initialize invoice header structure
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
            "total_aceptado": 0.0,
        }

        base_candidates, iva_candidates, total_candidates = [], [], []
        line_items: list[dict] = []

        # Parse entities returned by Document AI
        for e in doc.entities:
            t = e.type_
            text = e.mention_text or ""
            # Invoice-level fields
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
                total_candidates.append(text)
            elif t == "net_amount":
                base_candidates.append(text)
            elif t == "total_tax_amount":
                iva_candidates.append(text)
            elif t == "line_item":
                # Collect line item fields
                item = {
                    "descripcion": "",
                    "cantidad": 0.0,
                    "precio_unitario": 0.0,
                    "importe": 0.0,
                    "unidad": "",
                    "confidence": e.confidence,
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

        # Determine base, tax and total amounts by taking the maximum
        # candidate (common strategy to handle multiple candidates)
        data_factura["base"] = max([parse_amount(v) for v in base_candidates] or [0.0])
        data_factura["iva"] = max([parse_amount(v) for v in iva_candidates] or [0.0])
        data_factura["total"] = max([parse_amount(v) for v in total_candidates] or [0.0])

        # Basic validation: base + iva should equal total within a small tolerance
        suma = round(data_factura["base"] + data_factura["iva"], 2)
        data_factura["validacion"] = (
            "CORRECTA" if data_factura["total"] > 0 and abs(suma - data_factura["total"]) < 0.05 else "REVISAR"
        )

        facturas.append(data_factura)

        # If no line items, create a fallback line equal to the base amount
        if not line_items:
            line_items = [
                {
                    "descripcion": "TOTAL FACTURA",
                    "cantidad": 1,
                    "precio_unitario": data_factura["base"],
                    "importe": data_factura["base"],
                    "unidad": "",
                    "confidence": 1.0,
                }
            ]

        # Append line items with invoice context
        for idx, item in enumerate(line_items):
            lineas.append(
                {
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
                    "confidence": item["confidence"],
                }
            )

    return facturas, lineas