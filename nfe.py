# nfe.py
# ------------------------------------------------------------
# MÓDULO DE GERAÇÃO DE NF-e (XML FISCAL – SEM ASSINATURA / SEM SEFAZ)
# Compatível com backend Flask + Firestore
# ------------------------------------------------------------

import re
import datetime
from datetime import timezone
from lxml import etree


# ------------------------------------------------------------
# UTILITÁRIOS
# ------------------------------------------------------------
def somente_numeros(valor):
    if not valor:
        return ""
    return re.sub(r"\D", "", valor)


# ------------------------------------------------------------
# GERADOR DE XML DA NF-e
# ------------------------------------------------------------
def gerar_xml_nfe(venda, itens, ambiente="2"):
    """
    ambiente:
        "1" = Produção
        "2" = Homologação
    """

    NS = "http://www.portalfiscal.inf.br/nfe"

    # ------------------------------------------------------------
    # ROOT
    # ------------------------------------------------------------
    root = etree.Element("NFe", xmlns=NS)

    infNFe = etree.SubElement(
        root,
        "infNFe",
        Id="NFeTESTE00000000000000000000000000000000000000000",
        versao="4.00"
    )

    # ------------------------------------------------------------
    # IDE
    # ------------------------------------------------------------
    ide = etree.SubElement(infNFe, "ide")
    etree.SubElement(ide, "cUF").text = "29"  # Bahia
    etree.SubElement(ide, "natOp").text = "Venda de mercadoria"
    etree.SubElement(ide, "mod").text = "55"
    etree.SubElement(ide, "serie").text = "1"
    etree.SubElement(ide, "nNF").text = "1"
    etree.SubElement(ide, "tpNF").text = "1"
    etree.SubElement(ide, "dhEmi").text = datetime.datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S-00:00")
    etree.SubElement(ide, "tpAmb").text = ambiente
    etree.SubElement(ide, "finNFe").text = "1"
    etree.SubElement(ide, "indFinal").text = "1"
    etree.SubElement(ide, "indPres").text = "2"  # Internet
    etree.SubElement(ide, "procEmi").text = "0"
    etree.SubElement(ide, "verProc").text = "1.0"

    # ------------------------------------------------------------
    # EMITENTE
    # ------------------------------------------------------------
    emit = etree.SubElement(infNFe, "emit")
    etree.SubElement(emit, "CNPJ").text = "19291176000178"
    etree.SubElement(emit, "xNome").text = "Livraria e Distribuidora Rio Nilo Ltda"
    etree.SubElement(emit, "xFant").text = "Livraria Rio Nilo"

    enderEmit = etree.SubElement(emit, "enderEmit")
    etree.SubElement(enderEmit, "xLgr").text = "Avenida Aliomar Baleeiro"
    etree.SubElement(enderEmit, "nro").text = "2262"
    etree.SubElement(enderEmit, "xBairro").text = "Jardim Cajazeiras"
    etree.SubElement(enderEmit, "cMun").text = "2927408"
    etree.SubElement(enderEmit, "xMun").text = "Salvador"
    etree.SubElement(enderEmit, "UF").text = "BA"
    etree.SubElement(enderEmit, "CEP").text = "41230455"
    etree.SubElement(enderEmit, "cPais").text = "1058"
    etree.SubElement(enderEmit, "xPais").text = "BRASIL"

    etree.SubElement(emit, "IE").text = "113382426"
    etree.SubElement(emit, "CRT").text = "3"  # Lucro Presumido

    # ------------------------------------------------------------
    # DESTINATÁRIO
    # ------------------------------------------------------------
    dest = etree.SubElement(infNFe, "dest")
    cpf_limpo = somente_numeros(venda.get("cliente_cpf"))
    etree.SubElement(dest, "CPF").text = cpf_limpo
    etree.SubElement(dest, "xNome").text = venda.get(
        "cliente_nome", "Consumidor Final"
    )

    # ------------------------------------------------------------
    # PRODUTOS
    # ------------------------------------------------------------
    total_nf = 0.0

    for i, item in enumerate(itens, start=1):
        det = etree.SubElement(infNFe, "det", nItem=str(i))
        prod = etree.SubElement(det, "prod")

        nome = item.get("name", "Produto")
        preco_raw = str(item.get("price", "0"))
        preco_limpo = (
            preco_raw.replace("R$", "")
            .replace(" ", "")
            .replace(".", "")
            .replace(",", ".")
        )

        try:
            preco = float(preco_limpo)
        except Exception:
            preco = 0.0

        etree.SubElement(prod, "cProd").text = str(i)
        etree.SubElement(prod, "xProd").text = nome
        etree.SubElement(prod, "NCM").text = "49019900"
        etree.SubElement(prod, "CFOP").text = "5102"
        etree.SubElement(prod, "uCom").text = "UN"
        etree.SubElement(prod, "qCom").text = "1"
        etree.SubElement(prod, "vUnCom").text = f"{preco:.2f}"
        etree.SubElement(prod, "vProd").text = f"{preco:.2f}"

        total_nf += preco

    # ------------------------------------------------------------
    # TOTAL
    # ------------------------------------------------------------
    total = etree.SubElement(infNFe, "total")
    icmsTot = etree.SubElement(total, "ICMSTot")
    etree.SubElement(icmsTot, "vProd").text = f"{total_nf:.2f}"
    etree.SubElement(icmsTot, "vNF").text = f"{total_nf:.2f}"

    # ------------------------------------------------------------
    # RETORNO FINAL
    # ------------------------------------------------------------
    return etree.tostring(
        root,
        pretty_print=True,
        encoding="UTF-8",
        xml_declaration=True
    ).decode("utf-8")


# ------------------------------------------------------------
# ENVIO (SIMULADO)
# ------------------------------------------------------------
def enviar_nfe_sefaz(xml_assinado, ambiente="2"):
    return {
        "status": "teste",
        "mensagem": "NF-e simulada (SEFAZ ainda não chamado)",
        "xml": xml_assinado
    }
