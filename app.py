from flask import Flask, request, jsonify, send_file
from nfe import gerar_xml_nfe, assinar_xml_nfe, enviar_nfe_sefaz

from flask_cors import CORS
import requests
import json
import os
import datetime
import traceback
from dotenv import load_dotenv
from collections import defaultdict 
import io  # Para a exportação XLSX
from openpyxl import Workbook  # Para a exportação XLSX
from openpyxl.styles import Font, Alignment  # Para estilos em XLSX
import sys  # Para sys.exit caso a inicialização do Firebase falhe criticamente



from google.cloud import firestore

def obter_proximo_numero_nfe():
    ref = db.collection("nfe_config").document("controle")

    @firestore.transactional
    def transacao(transaction):
        snap = ref.get(transaction=transaction)

        if not snap.exists:
            raise RuntimeError("Documento nfe_config/controle não existe")

        dados = snap.to_dict()
        ultimo = dados.get("ultimo_numero", 0)
        serie = dados.get("serie", "2")

        proximo = ultimo + 1

        transaction.update(ref, {
            "ultimo_numero": proximo
        })

        return serie, proximo

    transaction = db.transaction()
    return transacao(transaction)


# ----- OneSignal Push Notifications -----
ONESIGNAL_APP_ID = "4e3346a9-bac1-4cbb-b366-4f17ffa4e0e4"
ONESIGNAL_API_KEY = "c7nli6j2wuuyuho2dwb5kai3w"  # Cole aqui

def enviar_notificacao(titulo, mensagem):
    url = "https://onesignal.com/api/v1/notifications"
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Basic {ONESIGNAL_API_KEY}",
    }
    payload = {
        "app_id": ONESIGNAL_APP_ID,
        "included_segments": ["All"],  # envia para TODOS que instalaram o app
        "headings": {"en": titulo},
        "contents": {"en": mensagem},
    }

    try:
        requests.post(url, headers=headers, json=payload)
        print("🔔 Notificação enviada com sucesso")
    except Exception as e:
        print("Erro ao enviar notificação:", e)


# ----- UltraMsg WhatsApp Notifications -----
ULTRAMSG_INSTANCE = "instance152238"         # da sua conta
ULTRAMSG_TOKEN = "saft20j5vof3157d"          # da sua conta

def enviar_whatsapp(numero, mensagem):
    """
    Envia uma mensagem de WhatsApp usando UltraMsg.
    Exemplo número: 5599999999999
    """
    try:
        url = f"https://api.ultramsg.com/{ULTRAMSG_INSTANCE}/messages/chat"

        payload = {
            "token": ULTRAMSG_TOKEN,
            "to": numero,
            "body": mensagem
        }

        headers = {"Content-Type": "application/json"}

        response = requests.post(url, json=payload, headers=headers)

        print("📨 UltraMsg resposta:", response.status_code, response.text)

        try:
            return response.json()
        except:
            return {"raw_response": response.text}

    except Exception as e:
        print("❌ Erro ao enviar mensagem WhatsApp:", e)
        return None


def gerar_mensagem_whatsapp(venda):
    from collections import defaultdict

    nome_comprador = venda.get("cliente_nome", "Cliente")
    numero_pedido = venda.get("merchant_order_id", "")
    nome_crianca = venda.get("nome_crianca", "Não informado")
    cpf = venda.get("cliente_cpf", "Não informado")
    forma_pagamento = venda.get("tipo_pagamento", "Não informado")
    escola = venda.get("cliente_escola", "Não informada")

    # 👇 AQUI VAMOS AJUSTAR DEPOIS QUE VOCÊ ENVIAR O JSON REAL DA VENDA
    parcelas = (
        venda.get("parcelas") or
        venda.get("installments") or
        venda.get("payment", {}).get("installments") or
        1
    )


    produtos = venda.get("produtos", [])

    # ──────────────────────────────────────────
    # AGRUPAR PRODUTOS IGUAIS ANTES DE LISTAR
    # ──────────────────────────────────────────
    produtos_agrupados = defaultdict(lambda: {"quantidade": 0, "preco": 0})

    for item in produtos:
        nome = item.get("name", "Produto")
        quantidade = item.get("quantity", 1)

        preco_raw = str(item.get("price", "0"))
        preco_limpo = (
            preco_raw.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
        )

        try:
            preco = float(preco_limpo)
        except:
            preco = 0.0

        produtos_agrupados[nome]["quantidade"] += quantidade
        produtos_agrupados[nome]["preco"] = preco

    # ──────────────────────────────────────────
    # MONTAR LISTA FINAL FORMATADA
    # ──────────────────────────────────────────
    lista_formatada = ""
    total = 0

    for nome, dados in produtos_agrupados.items():
        quantidade = dados["quantidade"]
        preco = dados["preco"]

        subtotal = quantidade * preco
        total += subtotal

        lista_formatada += f"📘 {nome} — {quantidade}x (R$ {subtotal:.2f})\n"

    # ──────────────────────────────────────────
    # MONTAR MENSAGEM FINAL
    # ──────────────────────────────────────────
    mensagem = f"""
Olá, {nome_comprador}! 👋

O pagamento do seu pedido nº *{numero_pedido}* foi *aprovado*. ✅

👦 *Nome da Criança:* {nome_crianca}
🪪 *CPF:* {cpf}
🏫 *Escola:* {escola}
💳 *Forma de Pagamento:* {forma_pagamento} em {parcelas}x

📦 *Produtos Comprados:*
{lista_formatada}
💵 *Total:* R$ {total:.2f}

🚨 *ATENÇÃO IMPORTANTE:*

O produto será entregue *dentro de 48 horas diretamente na escola*.

Para receber o kit do seu filho, *encaminhe esta mensagem para o WhatsApp da escola* ou apresente esta mensagem pessoalmente.

Obrigado por sua compra! 💙📚
"""

    return mensagem


load_dotenv()

app = Flask(__name__)
CORS(app, supports_credentials=True)


# Responde preflight automaticamente
@app.after_request
def apply_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

# Rota universal para OPTIONS (preflight)
@app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/<path:path>', methods=['OPTIONS'])
def cors_preflight(path):
    response = jsonify({"status": "ok"})
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

# ------------------------------------------------------
# Firebase
# ------------------------------------------------------

# Suas credenciais e URLs da Cielo
MERCHANT_ID = os.getenv("CIELO_MERCHANT_ID")
MERCHANT_KEY = os.getenv("CIELO_MERCHANT_KEY")

# URLs da API Cielo (Ajuste para SANDBOX ou PRODUÇÃO conforme suas credenciais)
CIELO_API_URL = os.getenv("CIELO_API_URL_PROD", "https://api.cieloecommerce.cielo.com.br/1/sales/")
CIELO_API_QUERY_URL = os.getenv("CIELO_API_QUERY_URL_PROD", "https://apiquery.cieloecommerce.cielo.com.br/1/sales/")

# --- Importações e Inicialização do Firebase ---
import firebase_admin
from firebase_admin import credentials, firestore
import json
import os

try:
    if not firebase_admin._apps:
        env_config = os.getenv('FIREBASE_CONFIG')
        
        if env_config:
            print(">>> [DEBUG] Tentando inicializar via Variável de Ambiente...")
            # Remove possíveis aspas extras que o Render às vezes coloca
            env_config = env_config.strip()
            if env_config.startswith("'") or env_config.startswith('"'):
                env_config = env_config[1:-1]
            
            creds_dict = json.loads(env_config)
            
            # Limpeza profunda da private_key
            if 'private_key' in creds_dict:
                # Substitui o literal \n por quebras de linha reais e remove espaços
                creds_dict['private_key'] = creds_dict['private_key'].replace('\\n', '\n').strip()
            
            cred = credentials.Certificate(creds_dict)
            firebase_admin.initialize_app(cred)
            print(">>> [SUCESSO] Firebase inicializado via ENV!")
        else:
            print(">>> [AVISO] Variável FIREBASE_CONFIG não detectada. Procurando arquivo...")
            # Só usa o arquivo se a variável de ambiente falhar
            if os.path.exists('chave-firebase.json'):
                cred = credentials.Certificate('chave-firebase.json')
                firebase_admin.initialize_app(cred)
                print(">>> [SUCESSO] Firebase inicializado via ARQUIVO LOCAL!")
            else:
                raise Exception("Nenhuma credencial encontrada (ENV ou Arquivo)!")

    db = firestore.client()
except Exception as e:
    print(f">>> [ERRO CRÍTICO] Falha total no Firebase: {e}")

@app.route('/')
def home():
    return "Backend Cielo funcionando! Acesse /processar-pagamento, /processar-pix, /processar-boleto ou /vendas (para testar o Firebase)."

# --- ROTAS DE PROCESSAMENTO DE PAGAMENTO (TODAS AGORA SALVAM UTC E CLIENTE_ESCOLA) ---

@app.route('/processar-pagamento', methods=['POST'])
def processar_pagamento():
    try:
        data = request.get_json()
        if not isinstance(data, dict) or not data:
            return jsonify({"error": "Dados inválidos ou incompletos. Esperado um objeto JSON."}), 400
        if 'paymentDetails' not in data or 'billingData' not in data:
            return jsonify({"error": "Dados inválidos ou incompletos"}), 400
        payment_details = data['paymentDetails']
        billing_data = data['billingData']
        if 'cardNumber' not in payment_details or 'holder' not in payment_details or \
           'expirationDate' not in payment_details or 'securityCode' not in payment_details or \
           'amount' not in payment_details:
            return jsonify({"error": "Dados de pagamento do cartão incompletos"}), 400

        card_number = payment_details['cardNumber'].replace(" ", "").replace("-", "")
        amount_in_cents = int(float(payment_details['amount']) * 100)
        installments = payment_details.get('installments', 1)

        merchant_order_id = f"LV_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"

        expiration_date_frontend_raw = payment_details['expirationDate']
        cielo_expiration_date = ""
        temp_numeric_date = expiration_date_frontend_raw.replace('/', '').replace(' ', '')
        month = temp_numeric_date[:2]
        year_part = temp_numeric_date[2:]
        try:
            if len(year_part) == 2:
                full_year_int = 2000 + int(year_part)
                full_year_int = max(full_year_int, datetime.datetime.now().year) 
            elif len(year_part) == 4:
                full_year_int = int(year_part)
            else:
                full_year_int = 0
            cielo_expiration_date = f"{month}/{full_year_int}"
        except ValueError:
            cielo_expiration_date = expiration_date_frontend_raw
        
        payment_data = {
            "MerchantOrderId": merchant_order_id,
            "Customer": {
                "Name": f"{billing_data.get('firstName')} {billing_data.get('lastName')}",
                "Identity": billing_data.get('cpf'),
                "IdentityType": "CPF",
                "Email": billing_data.get('email'),
                "Address": {
                    "Street": billing_data.get('address'),
                    "Number": billing_data.get('number'),
                    "Complement": billing_data.get('complement'),
                    "ZipCode": billing_data.get('zipCode'),
                    "District": billing_data.get('neighborhood'),
                    "City": billing_data.get('city', 'SAO PAULO'),
                    "State": billing_data.get('state', 'SP'),
                    "Country": billing_data.get('country', 'BRA')
                },
                "DeliveryAddress": {
                    "Street": billing_data.get('address'),
                    "Number": billing_data.get('number'),
                    "Complement": billing_data.get('complement'),
                    "ZipCode": billing_data.get('zipCode'),
                    "District": billing_data.get('neighborhood'),
                    "City": billing_data.get('city', 'SAO PAULO'),
                    "State": billing_data.get('state', 'SP'),
                    "Country": billing_data.get('country', 'BRA')
                }
            },
            "Payment": {
                "Type": "CreditCard",
                "Amount": amount_in_cents,
                "Installments": installments,
                "SoftDescriptor": "LIVRARIAWEB",
                "Capture": True,
                "CreditCard": {
                    "CardNumber": card_number,
                    "Holder": payment_details['holder'],
                    "ExpirationDate": cielo_expiration_date,
                    "SecurityCode": payment_details['securityCode'],
                    "Brand": "Visa"
                }
            }
        }

        headers = {
            "Content-Type": "application/json",
            "MerchantId": MERCHANT_ID,
            "MerchantKey": MERCHANT_KEY
        }

        response = requests.post(CIELO_API_URL, headers=headers, data=json.dumps(payment_data))
        response_json = response.json()

        if response.status_code == 201:
            if 'db' in globals() and db is not None:
                venda_data = {
                    "payment_id": response_json.get('Payment', {}).get('PaymentId'),
                    "merchant_order_id": merchant_order_id,
                    "data_hora": datetime.datetime.utcnow(), 
                    "produtos": data.get("cartItems", []) if data.get('cartItems') else 'N/A',
                    "cliente_nome": f"{billing_data.get('firstName', '')} {billing_data.get('lastName', '')}",
                    "nome_crianca": billing_data.get("fullNameChild", ""),
                    "cliente_cpf": billing_data.get("cpf", ""),

                    "cliente_email": billing_data.get('email', ''),
                    "cliente_escola": billing_data.get('school', 'N/A'),
                    "cliente_telefone": billing_data.get("phone", ""),
                    "valor": float(payment_details['amount']),
                    "status_cielo_codigo": response_json.get('Payment', {}).get('Status'),
                    "status_cielo_mensagem": response_json.get('Payment', {}).get('ReturnMessage', 'Status desconhecido'),
                    "tipo_pagamento": "Cartão de Crédito",
                    "bandeira": payment_details.get('brand', 'Visa'),

                    # ✅ AQUI, CORRETAMENTE FORMATADO
                    "parcelas": installments
                }

                db.collection('vendas').document().set(venda_data)

                    
                # 📲 WhatsApp removido — agora será enviado SOMENTE em /verificar-status


                enviar_notificacao(
                    "Venda aprovada!",
                    f"Você acabou de receber uma venda de R$ {payment_details['amount']}."
                )
            return jsonify({"status": "success", "message": "Pagamento com cartão processado com sucesso!", "cielo_response": response_json}), 200
        else:
            return jsonify({"status": "error", "message": "Erro ao processar pagamento com cartão na Cielo", "cielo_error": response_json}), response.status_code

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Erro interno no backend. Detalhes: {str(e)}"}), 500


@app.route('/processar-debito', methods=['POST'])
def processar_debito():
    try:
        data = request.get_json()
        if not isinstance(data, dict) or not data:
            return jsonify({"error": "Dados inválidos ou incompletos. Esperado um objeto JSON."}), 400
        if 'paymentDetails' not in data or 'billingData' not in data:
            return jsonify({"error": "Dados inválidos ou incompletos"}), 400

        payment_details = data['paymentDetails']
        billing_data = data['billingData']

        # Validação
        required_fields = ['cardNumber', 'holder', 'expirationDate', 'securityCode', 'amount']
        for field in required_fields:
            if field not in payment_details:
                return jsonify({"error": f"Campo obrigatório ausente: {field}"}), 400

        card_number = payment_details['cardNumber'].replace(" ", "").replace("-", "")
        amount_in_cents = int(float(payment_details['amount']) * 100)

        merchant_order_id = f"LV_DEBITO_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"

        # Formatar validade MM/AAAA
        raw_date = payment_details['expirationDate'].replace('/', '').strip()
        month = raw_date[:2]
        year = raw_date[2:]
        if len(year) == 2:
            year = f"20{year}"
        cielo_expiration_date = f"{month}/{year}"

        # 🔥 AGORA O DÉBITO VIRA CRÉDITO À VISTA (SEM 3DS)
        debit_as_credit_data = {
            "MerchantOrderId": merchant_order_id,
            "Customer": {
                "Name": f"{billing_data.get('firstName')} {billing_data.get('lastName')}",
                "Identity": billing_data.get('cpf'),
                "IdentityType": "CPF",
                "Email": billing_data.get('email')
            },
            "Payment": {
                "Type": "CreditCard",
                "Amount": amount_in_cents,
                "Installments": 1,
                "Capture": True,
                "SoftDescriptor": "LIVRARIAWEB",
                "CreditCard": {
                    "CardNumber": card_number,
                    "Holder": payment_details['holder'],
                    "ExpirationDate": cielo_expiration_date,
                    "SecurityCode": payment_details['securityCode'],
                    "Brand": "Visa"
                }
            }
        }

        headers = {
            "Content-Type": "application/json",
            "MerchantId": MERCHANT_ID,
            "MerchantKey": MERCHANT_KEY
        }

        response = requests.post(CIELO_API_URL, headers=headers, data=json.dumps(debit_as_credit_data))
        response_json = response.json()

        # SUCESSO
        if response.status_code == 201:
            if 'db' in globals() and db is not None:
                venda_data = {
                    "payment_id": response_json.get('Payment', {}).get('PaymentId'),
                    "merchant_order_id": merchant_order_id,
                    "data_hora": datetime.datetime.utcnow(),
                    "produtos": data.get("cartItems", []) if data.get('cartItems') else 'N/A',
                    "cliente_nome": f"{billing_data.get('firstName', '')} {billing_data.get('lastName', '')}",
                    "nome_crianca": billing_data.get("fullNameChild", ""),
                    "cliente_cpf": billing_data.get("cpf", ""),

                    "cliente_email": billing_data.get('email', ''),
                    "cliente_escola": billing_data.get('school', 'N/A'),
                    "cliente_telefone": billing_data.get("phone", ""),
                    "valor": float(payment_details['amount']),
                    "status_cielo_codigo": response_json.get('Payment', {}).get('Status'),
                    "status_cielo_mensagem": response_json.get('Payment', {}).get('ReturnMessage', 'Status desconhecido'),
                    "tipo_pagamento": "Débito (Processado como Crédito à Vista)",
                    "bandeira": "Visa"
                }
                db.collection('vendas').document().set(venda_data)

                    
               # 📲 WhatsApp removido — agora será enviado SOMENTE em /verificar-status

            return jsonify({
                "status": "success",
                "message": "Pagamento de débito aprovado!",
                "cielo_response": response_json
            }), 200
        
        # ERRO
        else:
            return jsonify({
                "status": "error",
                "message": "Erro ao processar débito na Cielo",
                "cielo_error": response_json
            }), response.status_code

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Erro interno no backend. Detalhes: {str(e)}"}), 500


@app.route('/processar-pix', methods=['POST'])
def processar_pix():
    try:
        data = request.get_json()
        if not isinstance(data, dict) or not data:
            return jsonify({"error": "Dados inválidos ou incompletos. Esperado um objeto JSON."}), 400
        if not data or 'paymentDetails' not in data or 'billingData' not in data:
            return jsonify({"error": "Dados inválidos ou incompletos"}), 400
        payment_details = data['paymentDetails']
        billing_data = data['billingData']
        amount_in_cents = int(float(payment_details['amount']) * 100)
        merchant_order_id = f"LV_PIX_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"

        pix_data = {
            "MerchantOrderId": merchant_order_id,
            "Customer": {
                "Name": f"{billing_data.get('firstName')} {billing_data.get('lastName')}",
                "Identity": billing_data.get('cpf'),
                "IdentityType": "CPF",
                "Email": billing_data.get('email')
            },
            "Payment": {
                "Type": "Pix",
                "Amount": amount_in_cents,
                "Pix": {
                    "ExpiresIn": 3600
                }
            }
        }

        headers = {
            "Content-Type": "application/json",
            "MerchantId": MERCHANT_ID,
            "MerchantKey": MERCHANT_KEY
        }

        response = requests.post(CIELO_API_URL, headers=headers, data=json.dumps(pix_data))
        response_json = response.json()

        if response.status_code == 201:
            qr_code_string = response_json.get('Payment', {}).get('QrCodeString')
            qr_code_image_url = response_json.get('Payment', {}).get('QrCodeImageUrl')
            if qr_code_string:
                if 'db' in globals() and db is not None:
                    venda_data = {
                        "payment_id": response_json.get('Payment', {}).get('PaymentId'),
                        "merchant_order_id": merchant_order_id,
                        "data_hora": datetime.datetime.utcnow(), 
                        "produtos": data.get("cartItems", []) if data.get('cartItems') else 'N/A', # Assumindo um único produto
                        "cliente_nome": f"{billing_data.get('firstName', '')} {billing_data.get('lastName', '')}",
                        "nome_crianca": billing_data.get("fullNameChild", ""),
                        "cliente_cpf": billing_data.get("cpf", ""),

                        "cliente_email": billing_data.get('email', ''),
                        "cliente_escola": billing_data.get('school', 'N/A'),
                        "cliente_telefone": billing_data.get("phone", ""), 
                        "valor": float(payment_details['amount']),
                        "status_cielo_codigo": response_json.get('Payment', {}).get('Status'),
                        "status_cielo_mensagem": response_json.get('Payment', {}).get('ReturnMessage', 'Status desconhecido'),
                        "status_interno": "Aguardando Pagamento PIX",
                        "tipo_pagamento": "PIX",
                        "qr_code_string": qr_code_string
                    }
                    db.collection('vendas').document().set(venda_data)
                return jsonify({"status": "success", "message": "QR Code Pix gerado com sucesso!", "cielo_response": response_json, "qr_code_string": qr_code_string, "qr_code_image_url": qr_code_image_url}), 200
            else:
                return jsonify({"status": "error", "message": "Erro ao gerar QR Code Pix: QR Code não encontrado na resposta.", "cielo_error": response_json}), response.status_code
        else:
            return jsonify({"status": "error", "message": "Erro ao processar Pix na Cielo", "cielo_error": response_json}), response.status_code

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Erro interno no backend. Detalhes: {str(e)}"}), 500

@app.route('/processar-boleto', methods=['POST'])
def processar_boleto():
    try:
        data = request.get_json()
        if not isinstance(data, dict) or not data:
            return jsonify({"error": "Dados inválidos ou incompletos. Esperado um objeto JSON."}), 400
        if not data or 'paymentDetails' not in data or 'billingData' not in data:
            return jsonify({"error": "Dados inválidos ou incompletos"}), 400
        payment_details = data['paymentDetails']
        billing_data = data['billingData']
        amount_in_cents = int(float(payment_details['amount']) * 100)
        merchant_order_id = f"LV_BOLETO_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"

        due_date = (datetime.date.today() + datetime.timedelta(days=5)).strftime('%Y-%m-%d')

        boleto_data = {
            "MerchantOrderId": merchant_order_id,
            "Customer": {
                "Name": f"{billing_data.get('firstName')} {billing_data.get('lastName')}",
                "Identity": billing_data.get('cpf'),
                "IdentityType": "CPF",
                "Email": billing_data.get('email')
            },
            "Payment": {
                "Type": "Boleto",
                "Amount": amount_in_cents,
                "BoletoNumber": f"000000{os.urandom(3).hex()}",
                "Demonstrative": "Pagamento referente à compra na Livraria Web",
                "Instructions": "Não receber após o vencimento",
                "Provider": "Bradesco",
                "ExpirationDate": due_date
            }
        }

        headers = {
            "Content-Type": "application/json",
            "MerchantId": MERCHANT_ID,
            "MerchantKey": MERCHANT_KEY
        }

        response = requests.post(CIELO_API_URL, headers=headers, data=json.dumps(boleto_data))
        response_json = response.json()

        if response.status_code == 201:
            boleto_url = response_json.get('Payment', {}).get('Url')
            bar_code_number = response_json.get('Payment', {}).get('BarCodeNumber')
            digitable_line = response_json.get('Payment', {}).get('DigitableLine')
            if boleto_url:
                if 'db' in globals() and db is not None:
                    venda_data = {
                        "payment_id": response_json.get('Payment', {}).get('PaymentId'),
                        "merchant_order_id": merchant_order_id,
                        "data_hora": datetime.datetime.utcnow(), 
                        "produtos": data.get("cartItems", []) if data.get('cartItems') else 'N/A', # Assumindo um único produto
                        "cliente_nome": f"{billing_data.get('firstName', '')} {billing_data.get('lastName', '')}",
                        "nome_crianca": billing_data.get("fullNameChild", ""),
                        "cliente_cpf": billing_data.get("cpf", ""),

                        "cliente_email": billing_data.get('email', ''),
                        "cliente_escola": billing_data.get('school', 'N/A'), 
                        "cliente_telefone": billing_data.get("phone", ""),
                        "valor": float(payment_details['amount']),
                        "status_cielo_codigo": response_json.get('Payment', {}).get('Status'),
                        "status_cielo_mensagem": response_json.get('Payment', {}).get('ReturnMessage', 'Status desconhecido'),
                        "status_interno": "Aguardando Pagamento Boleto",
                        "tipo_pagamento": "Boleto",
                        "boleto_url": boleto_url,
                        "bar_code_number": bar_code_number,
                        "digitable_line": digitable_line
                    }
                    db.collection('vendas').document().set(venda_data)
                return jsonify({"status": "success", "message": "Boleto gerado com sucesso!", "cielo_response": response_json, "boleto_url": boleto_url, "bar_code_number": bar_code_number, "digitable_line": digitable_line}), 200
            else:
                return jsonify({"status": "error", "message": "Erro ao gerar Boleto: URL do boleto não encontrada na resposta.", "cielo_error": response_json}), response.status_code
        else:
            return jsonify({"status": "error", "message": "Erro ao processar Boleto na Cielo", "cielo_error": response_json}), response.status_code

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Erro interno no backend. Detalhes: {str(e)}"}), 500


@app.route('/verificar-status/<payment_id>', methods=['GET'])
def verificar_status(payment_id):
    """
    Verifica o status do pagamento na Cielo.
    Se o status for 2 (pago), envia WhatsApp e atualiza o Firestore.
    """
    try:
        # Consulta na API da Cielo
        url = f"{CIELO_API_QUERY_URL}/{payment_id}"
        headers = {
            "Content-Type": "application/json",
            "MerchantId": MERCHANT_ID,
            "MerchantKey": MERCHANT_KEY
        }

        response = requests.get(url, headers=headers)
        data = response.json()

        payment_status = data.get("Payment", {}).get("Status")

        # Se o pagamento ainda não foi aprovado
        if payment_status != 2:
            return jsonify({"status": payment_status}), 200

        # Pagamento aprovado → buscar venda no Firestore
        vendas = db.collection("vendas").where("payment_id", "==", payment_id).stream()
        for v in vendas:
            venda_data = v.to_dict()
            doc_id = v.id

            # Já enviamos WhatsApp antes?
            if venda_data.get("whatsapp_enviado", False):
                return jsonify({"status": payment_status}), 200

            # Enviar WhatsApp
            numero_cliente = venda_data.get("cliente_telefone", None)

            if numero_cliente and not numero_cliente.startswith("55"):
                numero_cliente = "55" + numero_cliente
            if not numero_cliente:
                print("⚠️ Venda não tem telefone salvo")
                return jsonify({"status": payment_status}), 200

            mensagem = gerar_mensagem_whatsapp(venda_data)
            enviar_whatsapp(numero_cliente, mensagem)

            # Marcar como enviado
            db.collection("vendas").document(doc_id).update({
                "whatsapp_enviado": True
            })

        return jsonify({"status": payment_status}), 200

    except Exception as e:
        print("Erro ao verificar status:", e)
        return jsonify({"error": str(e)}), 500

# ROTA PARA OBTER VENDAS GERAIS (USADA NO DASHBOARD E MINHAS VENDAS)
@app.route('/vendas', methods=['GET'])
def get_vendas():
    try:
        if 'db' not in globals() or db is None:
            return jsonify({"error": "Serviço de banco de dados indisponível."}), 500

        # --- PARÂMETROS ---
        period = request.args.get('period', 'today') 
        school_filter = request.args.get('school') # <--- NOVO: Recebe o filtro do front
        
        now_utc = datetime.datetime.utcnow() 
        start_date = None
        end_date = None

        # --- LÓGICA DE DATAS ---
        if period == 'today':
            start_date = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now_utc.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif period == 'yesterday':
            yesterday_utc = now_utc - datetime.timedelta(days=1)
            start_date = yesterday_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = yesterday_utc.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif period == 'last7days':
            start_date = now_utc - datetime.timedelta(days=6)
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now_utc.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif period == 'currentMonth':
            start_date = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            next_month = (now_utc.replace(day=1) + datetime.timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = next_month - datetime.timedelta(microseconds=1)
        elif period == 'lastMonth':
            first_day_current_month = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = first_day_current_month - datetime.timedelta(microseconds=1)
            start_date = end_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # --- QUERY ---
        vendas_query = db.collection('vendas')

        # 1. 🔥 OTIMIZAÇÃO CRÍTICA: Filtra por escola no BANCO DE DADOS
        if school_filter:
            # Decodifica string caso necessário (ex: espaços %20)
            school_clean = requests.utils.unquote(school_filter)
            vendas_query = vendas_query.where('cliente_escola', '==', school_clean)

        # 2. Filtros de Data
        if start_date:
            vendas_query = vendas_query.where('data_hora', '>=', start_date)
        if end_date:
            vendas_query = vendas_query.where('data_hora', '<=', end_date)
            
        # 3. Proteção contra Crash: Se não tem filtro de escola e pede "tudo", limita a 100
        if not school_filter and period == 'allTime':
             vendas_query = vendas_query.limit(100)

        docs = vendas_query.stream()
        
        lista_vendas = []
        for doc in docs:
            venda = doc.to_dict()
            venda['id'] = doc.id
            
            # Tratamento de data
            if isinstance(venda.get('data_hora'), datetime.datetime):
                venda['data_hora'] = venda['data_hora'].isoformat() 
            else:
                venda['data_hora'] = None 
            
            lista_vendas.append(venda)
        
        # Ordenação final em memória (seguro agora pois a lista é pequena)
        lista_vendas.sort(key=lambda x: x.get('data_hora', '0000-01-01T00:00:00') if x.get('data_hora') else '', reverse=True)
        
        print(f"Retornando {len(lista_vendas)} vendas. Filtro Escola: {school_filter}")
        return jsonify(lista_vendas), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Erro ao carregar vendas: {str(e)}"}), 500

# --- NOVAS ROTAS PARA RELATÓRIOS ESPECÍFICOS ---

@app.route('/relatorios/receita-por-produto', methods=['GET'])
def get_receita_por_produto():
    try:
        if 'db' not in globals() or db is None:
            return jsonify({"error": "Serviço de banco de dados indisponível."}), 500

        vendas_ref = db.collection('vendas')
        docs = vendas_ref.stream()

        produtos_data = defaultdict(lambda: {'quantidade': 0, 'receita': 0.0})

        for doc in docs:
            venda = doc.to_dict()
            # Considera vendas aprovadas, Pix gerado ou Boleto emitido
            if venda.get('status_cielo_codigo') in [2, 12, 1]: 
                produtos = venda.get('produtos', [])

                if not isinstance(produtos, list) or len(produtos) == 0:
                    continue

                produto_nome = produtos[0].get('name', 'Produto Desconhecido')
                valor = float(venda.get('valor', 0))

                produtos_data[produto_nome]['quantidade'] += 1
                produtos_data[produto_nome]['receita'] += valor


        lista_produtos = []
        for nome, dados in produtos_data.items():
            lista_produtos.append({
                'nome': nome,
                'quantidade_vendida': dados['quantidade'],
                'receita_gerada': dados['receita']
            })
        
        lista_produtos.sort(key=lambda x: x['receita_gerada'], reverse=True)

        return jsonify(lista_produtos), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Erro ao gerar relatório: {str(e)}"}), 500

@app.route('/relatorios/escolas', methods=['GET'])
def get_relatorio_escolas():
    try:
        if 'db' not in globals() or db is None:
            return jsonify({"error": "Serviço de banco de dados indisponível."}), 500

        vendas_ref = db.collection('vendas')
        docs = vendas_ref.stream()

        escolas_resumo = defaultdict(lambda: {
            'total_vendas': 0,
            'receita_total': 0.0,
            'produtos_vendidos': defaultdict(int), 
            'alunos_compraram_ids': set() 
        })
        
        for doc in docs:
            venda = doc.to_dict()
            escola_nome = venda.get('cliente_escola', 'Escola Desconhecida')
            cliente_email = venda.get('cliente_email', 'Aluno Desconhecido') 
            produto_nome = (
                venda.get('produtos')[0].get('name')
                if isinstance(venda.get('produtos'), list) and len(venda.get('produtos')) > 0
                else 'Produto Desconhecido'
            )

            valor = float(venda.get('valor', 0))

            if venda.get('status_cielo_codigo') == 2:  # SOMENTE APROVADAS
                escolas_resumo[escola_nome]['total_vendas'] += 1
                escolas_resumo[escola_nome]['receita_total'] += valor
                escolas_resumo[escola_nome]['produtos_vendidos'][produto_nome] += 1
                escolas_resumo[escola_nome]['alunos_compraram_ids'].add(cliente_email)


        resumo_escolas_lista = []
        for nome_escola, dados_escola in escolas_resumo.items():
            mais_vendido = None
            if dados_escola['produtos_vendidos']:
                mais_vendido = max(dados_escola['produtos_vendidos'], key=dados_escola['produtos_vendidos'].get)

            resumo_escolas_lista.append({
                'nome_escola': nome_escola,
                'total_vendas': dados_escola['total_vendas'],
                'receita_total': dados_escola['receita_total'],
                'produto_mais_vendido': mais_vendido,
                'quantidade_alunos_compraram': len(dados_escola['alunos_compraram_ids']) 
            })
        
        resumo_escolas_lista.sort(key=lambda x: x['receita_total'], reverse=True)

        return jsonify(resumo_escolas_lista), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Erro ao gerar relatório: {str(e)}"}), 500

@app.route('/relatorios/escola/<string:nome_escola_url>', methods=['GET'])
def get_vendas_por_escola(nome_escola_url):
    try:
        if 'db' not in globals() or db is None:
            return jsonify({"error": "Serviço de banco de dados indisponível."}), 500

        nome_escola = requests.utils.unquote(nome_escola_url) 

        # Adiciona order_by para garantir que o índice composto seja usado e a ordem seja consistente
        vendas_query = db.collection('vendas').where('cliente_escola', '==', nome_escola) \
                         .order_by('data_hora', direction=firestore.Query.DESCENDING).stream()
        
        vendas_detalhadas = []
        alunos_map = set()

        for doc in vendas_query:
            venda = doc.to_dict()

            # ✅ MOSTRAR SOMENTE VENDAS APROVADAS
            if venda.get('status_cielo_codigo') != 2:
                continue


            aluno_nome = venda.get('cliente_nome')
            if not aluno_nome:
                continue

            chave_aluno = aluno_nome.strip().lower()

    # 👉 Se já existe, ignora
            if chave_aluno in alunos_map:
                continue

            alunos_map.add(chave_aluno)


            # 🟢 PRODUTO (SEGURO)
            produto_nome = 'N/A'
            produtos = venda.get('produtos', [])

            if isinstance(produtos, list) and len(produtos) > 0:
                produto_nome = produtos[0].get('name', 'N/A')

            data_compra_iso = None
            if isinstance(venda.get('data_hora'), datetime.datetime):
                data_compra_iso = venda['data_hora'].isoformat()
            else:
                data_compra_iso = 'N/A'

            


            vendas_detalhadas.append({
                'aluno': aluno_nome,
                'escola': venda.get('cliente_escola', 'N/A'),
                'produto': produto_nome,
                'valor': float(venda.get('valor', 0)),
                'data_compra': data_compra_iso
            })

        
        return jsonify(vendas_detalhadas), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Erro ao buscar vendas para escola: {str(e)}"}), 500

@app.route('/relatorios/escola/exportar_xlsx/<string:nome_escola_url>', methods=['GET'])
def exportar_alunos_xlsx(nome_escola_url):
    try:
        if 'db' not in globals() or db is None:
            return jsonify({"error": "Serviço de banco de dados indisponível."}), 500

        nome_escola = requests.utils.unquote(nome_escola_url) 
        print(f"Iniciando exportação XLSX para a escola: '{nome_escola}'")

        # Busca vendas ordenadas
        vendas_query = db.collection('vendas').where('cliente_escola', '==', nome_escola) \
                         .order_by('data_hora', direction=firestore.Query.DESCENDING).stream()
        
        vendas_detalhadas_para_export = []
        alunos_map = set() # Controle de duplicidade

        for doc in vendas_query:
            venda = doc.to_dict()
            
            # --- CORREÇÃO 1: FILTRO DE STATUS ---
            # Só aceita vendas APROVADAS (Código 2 na Cielo)
            if venda.get('status_cielo_codigo') != 2:
                continue

            # --- CORREÇÃO 2: REMOÇÃO DE DUPLICIDADE ---
            # Garante que não apareça o mesmo aluno duas vezes (mesma lógica da tela visual)
            aluno_nome = venda.get('cliente_nome')
            if not aluno_nome:
                continue

            chave_aluno = aluno_nome.strip().lower()
            if chave_aluno in alunos_map:
                continue # Pula se o aluno já foi processado nesta lista
            
            alunos_map.add(chave_aluno)

            # --- PREPARAÇÃO DOS DADOS ---
            data_compra_excel = venda.get('data_hora') 
            
            if isinstance(data_compra_excel, datetime.datetime):
                if data_compra_excel.tzinfo is not None and data_compra_excel.tzinfo.utcoffset(data_compra_excel) is not None:
                    data_compra_excel = data_compra_excel.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            else:
                data_compra_excel = str(data_compra_excel) 

            # Uso de float seguro ou 0
            try:
                valor_excel = float(venda.get('valor', 0))
            except:
                valor_excel = 0.0

            # Nome do produto seguro
            produtos = venda.get('produtos', [])
            nome_produto = 'N/A'
            if isinstance(produtos, list) and len(produtos) > 0:
                 nome_produto = produtos[0].get('name', 'N/A')

            vendas_detalhadas_para_export.append({
                'aluno': aluno_nome, # Já validado acima
                'escola': venda.get('cliente_escola', 'N/A'),
                'produto': nome_produto,
                'valor': valor_excel,
                'data_compra': data_compra_excel 
            })
        
        if not vendas_detalhadas_para_export:
            # Retorna um erro amigável se não houver vendas aprovadas para exportar
            return jsonify({"error": "Não há vendas aprovadas para exportar nesta escola."}), 404

        # --- GERAÇÃO DO ARQUIVO EXCEL ---
        wb = Workbook()
        ws = wb.active 
        ws.title = f"Vendas_{nome_escola}"[:31] 

        headers = ["Aluno", "Escola", "Produto", "Valor (R$)", "Data Compra"]
        ws.append(headers)

        header_font = Font(bold=True)
        for col_idx, cell in enumerate(ws[1], 1): 
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center') 
            ws.column_dimensions[cell.column_letter].width = len(headers[col_idx-1]) + 5 

        for row_data in vendas_detalhadas_para_export:
            ws.append([
                row_data['aluno'],
                row_data['escola'],
                row_data['produto'],
                row_data['valor'], 
                row_data['data_compra']
            ])
        
        # Formatação das células (Largura automática e formato de moeda/data)
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter 
            for cell in col:
                try: 
                    if cell.value is not None:
                        cell_value_str = str(cell.value)
                        
                        if column == 'D': # Coluna Valor 
                           cell.number_format = '"R$"#,##0.00' 
                        elif column == 'E' and isinstance(cell.value, datetime.datetime): # Coluna Data
                           cell.number_format = 'DD/MM/YYYY HH:MM:SS' 
                           cell_value_str = cell.value.strftime('%Y-%m-%d %H:%M:%S') 
                        
                        if len(cell_value_str) > max_length:
                            max_length = len(cell_value_str)
                except Exception:
                    pass
            adjusted_width = (max_length + 2) 
            ws.column_dimensions[column].width = min(adjusted_width, 70) 

        ws.auto_filter.ref = ws.dimensions

        output = io.BytesIO()
        wb.save(output)
        output.seek(0) 

        filename = f"relatorio_alunos_{nome_escola.replace(' ', '_')}_{datetime.date.today().strftime('%Y%m%d')}.xlsx"
        
        print(f"Exportação XLSX para '{nome_escola}' concluída com sucesso.")
        return send_file(output,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         download_name=filename, 
                         as_attachment=True)

    except Exception as e:
        print(f"Erro ao exportar para XLSX para escola '{nome_escola}': {e}")
        traceback.print_exc()
        return jsonify({"error": f"Erro ao exportar para XLSX: {str(e)}"}), 500

# ... (SEU CÓDIGO EXISTENTE DE TODAS AS OUTRAS ROTAS, INCLUINDO /relatorios/escola/exportar_xlsx) ...

# --- NOVA ROTA PARA DADOS FINANCEIROS CONSOLIDADOS ---
@app.route('/financeiro/resumo', methods=['GET'])
def get_financeiro_resumo():
    try:
        if 'db' not in globals() or db is None:
            return jsonify({"error": "Serviço de banco de dados indisponível."}), 500

        vendas_ref = db.collection('vendas')
        docs = vendas_ref.stream()

        valor_ganho = 0.0
        valor_reembolsado = 0.0
        # defaultdict para somar valores por método de pagamento
        metodos_pagamento_totais = defaultdict(float) 

        for doc in docs:
            venda = doc.to_dict()
            status_cielo_codigo = venda.get('status_cielo_codigo')
            valor = float(venda.get('valor', 0))
            tipo_pagamento = venda.get('tipo_pagamento', 'Outro') # Captura o tipo de pagamento

            # Contabiliza para Valor Ganho e Totais por Método (status 2, 12, 1)
            # Status 2: Capturada/Aprovada
            # Status 12: Pix Gerado (Aguardando pagamento - considerado ganho potencial)
            # Status 1: Boleto Emitido (Aguardando pagamento - considerado ganho potencial)
            if status_cielo_codigo in [2, 12, 1]: 
                valor_ganho += valor
                metodos_pagamento_totais[tipo_pagamento] += valor
            elif status_cielo_codigo == 3: # Status 3: Reembolsada/Cancelada
                valor_reembolsado += valor

        # Calcular porcentagens para cada método de pagamento
        metodos_pagamento_percentuais = {}
        for metodo, total_metodo in metodos_pagamento_totais.items():
            percentual = (total_metodo / valor_ganho) if valor_ganho > 0 else 0
            metodos_pagamento_percentuais[metodo] = {
                'valor': total_metodo,
                'percentual': percentual
            }

        # Formatar a saída
        resumo_financeiro = {
            'valor_ganho': valor_ganho,
            'valor_reembolsado': valor_reembolsado,
            'metodos_pagamento': metodos_pagamento_percentuais
        }

        return jsonify(resumo_financeiro), 200

    except Exception as e:
        print(f"Erro ao gerar resumo financeiro: {e}")
        traceback.print_exc()
        return jsonify({"error": f"Erro ao gerar resumo financeiro: {str(e)}"}), 500



from nfe import (
    gerar_xml_nfe,
    assinar_xml_nfe,
    enviar_nfe_sefaz
)

@app.route("/emitir-nfe", methods=["POST"])
def emitir_nfe():
    try:
        data = request.get_json()
        payment_id = data.get("payment_id")

        if not payment_id:
            return jsonify({"error": "payment_id não informado"}), 400

        vendas = db.collection("vendas").where("payment_id", "==", payment_id).stream()

        venda_doc = None
        venda_ref = None
        for v in vendas:
            venda_doc = v.to_dict()
            venda_ref = v.reference
            break

        if not venda_doc:
            return jsonify({"error": "Venda não encontrada"}), 404

        if venda_doc.get("status_cielo_codigo") != 2:
            return jsonify({
                "error": "NF-e só pode ser emitida para pagamento aprovado"
            }), 400

        venda = {
            "cliente_nome": venda_doc["cliente_nome"],
            "cliente_cpf": venda_doc["cliente_cpf"]
        }

        itens = venda_doc.get("produtos", [])

        xml = gerar_xml_nfe(
            venda=venda,
            itens=itens,
            ambiente="2",  # HOMOLOGAÇÃO
            serie="2",
            numero_nfe="1"
        )

        xml_assinado = assinar_xml_nfe(xml)

        retorno = enviar_nfe_sefaz(xml_assinado, ambiente="2")

        if retorno.get("status") == "autorizada":
            venda_ref.update({
                "nfe_emitida": True,
                "nfe_xml": retorno["xml_autorizado"],
                "nfe_chave": retorno.get("chNFe"),
                "nfe_emitida_em": datetime.datetime.utcnow()
            })

        return jsonify(retorno), 200

    except Exception as e:
        return jsonify({
            "error": "Erro ao emitir NF-e",
            "detalhes": str(e)
        }), 500


# ... (ESTA LINHA ABAIXO É ONDE SEU `if __name__ == '__main__':` DEVE ESTAR) ...
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
