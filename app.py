from flask import Flask, request, jsonify, send_file
# from nfe import gerar_xml_nfe, assinar_xml_nfe, enviar_nfe_sefaz
from flask_cors import CORS
import requests
import json
import os
import datetime
import traceback
from dotenv import load_dotenv
from collections import defaultdict 
import io
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
import sys
import re

# --- Importações e Inicialização do Firebase ---
import firebase_admin
from firebase_admin import credentials, firestore

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

@app.after_request
def apply_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

@app.route('/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/<path:path>', methods=['OPTIONS'])
def cors_preflight(path):
    response = jsonify({"status": "ok"})
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

# ------------------------------------------------------
# Configuração do Firebase
# ------------------------------------------------------
try:
    if not firebase_admin._apps:
        env_config = os.getenv('FIREBASE_CONFIG')
        if env_config:
            print(">>> [DEBUG] Inicializando Firebase via ENV...")
            env_config = env_config.strip()
            if env_config.startswith("'") or env_config.startswith('"'):
                env_config = env_config[1:-1]
            creds_dict = json.loads(env_config)
            if 'private_key' in creds_dict:
                creds_dict['private_key'] = creds_dict['private_key'].replace('\\n', '\n').strip()
            cred = credentials.Certificate(creds_dict)
            firebase_admin.initialize_app(cred)
        else:
            print(">>> [AVISO] ENV não detectada. Tentando arquivo local...")
            if os.path.exists('chave-firebase.json'):
                cred = credentials.Certificate('chave-firebase.json')
                firebase_admin.initialize_app(cred)
            else:
                raise Exception("Nenhuma credencial encontrada (ENV ou Arquivo)!")
    db = firestore.client()
except Exception as e:
    print(f">>> [ERRO CRÍTICO] Falha total no Firebase: {e}")

# ------------------------------------------------------
# Configurações de API (Cielo, Notificações)
# ------------------------------------------------------
MERCHANT_ID = os.getenv("CIELO_MERCHANT_ID")
MERCHANT_KEY = os.getenv("CIELO_MERCHANT_KEY")
CIELO_API_URL = os.getenv("CIELO_API_URL_PROD", "https://api.cieloecommerce.cielo.com.br/1/sales/")
CIELO_API_QUERY_URL = os.getenv("CIELO_API_QUERY_URL_PROD", "https://apiquery.cieloecommerce.cielo.com.br/1/sales/")

ONESIGNAL_APP_ID = "4e3346a9-bac1-4cbb-b366-4f17ffa4e0e4"
ONESIGNAL_API_KEY = "c7nli6j2wuuyuho2dwb5kai3w"
ULTRAMSG_INSTANCE = "instance152238"
ULTRAMSG_TOKEN = "saft20j5vof3157d"

"""
def obter_proximo_numero_nfe():
    ref = db.collection("nfe_config").document("controle")
    @firestore.transactional
    def transacao(transaction):
        snap = ref.get(transaction=transaction)
        if not snap.exists: raise RuntimeError("Documento nfe_config/controle não existe")
        dados = snap.to_dict()
        ultimo = dados.get("ultimo_numero", 0)
        serie = dados.get("serie", "2")
        proximo = ultimo + 1
        transaction.update(ref, {"ultimo_numero": proximo})
        return serie, proximo
    transaction = db.transaction()
    return transacao(transaction)
"""
def enviar_notificacao(titulo, mensagem):
    try:
        url = "https://onesignal.com/api/v1/notifications"
        headers = {"Content-Type": "application/json; charset=utf-8", "Authorization": f"Basic {ONESIGNAL_API_KEY}"}
        payload = {"app_id": ONESIGNAL_APP_ID, "included_segments": ["All"], "headings": {"en": titulo}, "contents": {"en": mensagem}}
        requests.post(url, headers=headers, json=payload)
    except Exception as e: print("Erro Notification:", e)

def enviar_whatsapp(numero, mensagem):
    try:
        url = f"https://api.ultramsg.com/{ULTRAMSG_INSTANCE}/messages/chat"
        payload = {"token": ULTRAMSG_TOKEN, "to": numero, "body": mensagem}
        requests.post(url, json=payload, headers={"Content-Type": "application/json"})
    except Exception as e: print("Erro WhatsApp:", e)

def gerar_mensagem_whatsapp(venda):
    from collections import defaultdict
    nome_comprador = venda.get("cliente_nome", "Cliente")
    numero_pedido = venda.get("merchant_order_id", "")
    nome_crianca = venda.get("nome_crianca", "Não informado")
    escola = venda.get("cliente_escola", "Não informada")
    produtos = venda.get("produtos", [])
    
    produtos_agrupados = defaultdict(lambda: {"quantidade": 0, "preco": 0})
    for item in produtos:
        nome = item.get("name", "Produto")
        qtd = int(item.get("quantity", 1))
        try: p = float(str(item.get("price", "0")).replace("R$", "").replace(",", "."))
        except: p = 0.0
        produtos_agrupados[nome]["quantidade"] += qtd
        produtos_agrupados[nome]["preco"] = p

    lista = ""
    total = 0
    for nome, dados in produtos_agrupados.items():
        sub = dados["quantidade"] * dados["preco"]
        total += sub
        lista += f"📘 {nome} — {dados['quantidade']}x (R$ {sub:.2f})\n"

    return f"Olá, {nome_comprador}! 👋\nPedido *{numero_pedido}* aprovado. ✅\n\n👦 *Criança:* {nome_crianca}\n🏫 *Escola:* {escola}\n\n📦 *Itens:*\n{lista}\n💵 *Total:* R$ {total:.2f}\n\n🚨 *Importante:* O produto será entregue na escola."

@app.route('/')
def home():
    return "Backend Rio Nilo Ativo (Versão Full)!"

# ==============================================================================
#  ROTAS DE PAGAMENTO (BLINDADAS CONTRA DUPLICIDADE)
# ==============================================================================

@app.route('/processar-pagamento', methods=['POST'])
def processar_pagamento():
    try:
        data = request.get_json()
        payment_details = data['paymentDetails']
        billing_data = data['billingData']
        
        merchant_order_id = f"LV_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"
        raw_date = payment_details['expirationDate'].replace('/', '').strip()
        cielo_date = f"{raw_date[:2]}/{'20' + raw_date[2:] if len(raw_date[2:]) == 2 else raw_date[2:]}"
        
        payment_data = {
            "MerchantOrderId": merchant_order_id,
            "Customer": {"Name": f"{billing_data.get('firstName')} {billing_data.get('lastName')}", "Identity": billing_data.get('cpf'), "Email": billing_data.get('email')},
            "Payment": {
                "Type": "CreditCard", "Amount": int(float(payment_details['amount']) * 100), "Installments": payment_details.get('installments', 1),
                "Capture": True, "SoftDescriptor": "LIVRARIAWEB",
                "CreditCard": {"CardNumber": payment_details['cardNumber'].replace(" ", "").replace("-", ""), "Holder": payment_details['holder'], "ExpirationDate": cielo_date, "SecurityCode": payment_details['securityCode'], "Brand": "Visa"}
            }
        }
        
        response = requests.post(CIELO_API_URL, headers={"Content-Type": "application/json", "MerchantId": MERCHANT_ID, "MerchantKey": MERCHANT_KEY}, data=json.dumps(payment_data))
        response_json = response.json()

        if response.status_code == 201:
            pid = response_json.get('Payment', {}).get('PaymentId')
            venda_data = {
                "payment_id": pid,
                "merchant_order_id": merchant_order_id,
                "data_hora": datetime.datetime.utcnow(),
                "produtos": data.get("cartItems", []) or [],
                "cliente_nome": f"{billing_data.get('firstName')} {billing_data.get('lastName')}",
                "nome_crianca": billing_data.get("fullNameChild", ""),
                "cliente_cpf": billing_data.get("cpf", ""),
                "cliente_email": billing_data.get('email', ''),
                "cliente_escola": billing_data.get('school', 'N/A'),
                "cliente_telefone": billing_data.get("phone", ""),
                "valor": float(payment_details['amount']),
                "status_cielo_codigo": response_json.get('Payment', {}).get('Status'),
                "status_cielo_mensagem": response_json.get('Payment', {}).get('ReturnMessage', 'Desconhecido'),
                "tipo_pagamento": "Cartão de Crédito",
                "parcelas": payment_details.get('installments', 1),
                "bandeira": "Visa"
            }
            db.collection('vendas').document(str(pid)).set(venda_data)
            enviar_notificacao("Venda Aprovada!", f"Venda de R$ {payment_details['amount']}")
            return jsonify({"status": "success", "cielo_response": response_json}), 200
        else:
            return jsonify({"status": "error", "cielo_error": response_json}), response.status_code
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/processar-debito', methods=['POST'])
def processar_debito():
    try:
        data = request.get_json()
        payment_details = data['paymentDetails']
        billing_data = data['billingData']
        merchant_order_id = f"LV_DEBITO_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"
        
        raw_date = payment_details['expirationDate'].replace('/', '').strip()
        cielo_date = f"{raw_date[:2]}/{'20' + raw_date[2:] if len(raw_date[2:]) == 2 else raw_date[2:]}"

        debit_data = {
            "MerchantOrderId": merchant_order_id,
            "Customer": {"Name": f"{billing_data.get('firstName')} {billing_data.get('lastName')}", "Identity": billing_data.get('cpf'), "Email": billing_data.get('email')},
            "Payment": {
                "Type": "CreditCard", "Amount": int(float(payment_details['amount']) * 100), "Installments": 1, "Capture": True, "SoftDescriptor": "LIVRARIAWEB",
                "CreditCard": {"CardNumber": payment_details['cardNumber'].replace(" ", "").replace("-", ""), "Holder": payment_details['holder'], "ExpirationDate": cielo_date, "SecurityCode": payment_details['securityCode'], "Brand": "Visa"}
            }
        }
        
        response = requests.post(CIELO_API_URL, headers={"Content-Type": "application/json", "MerchantId": MERCHANT_ID, "MerchantKey": MERCHANT_KEY}, data=json.dumps(debit_data))
        response_json = response.json()

        if response.status_code == 201:
            pid = response_json.get('Payment', {}).get('PaymentId')
            venda_data = {
                "payment_id": pid,
                "merchant_order_id": merchant_order_id,
                "data_hora": datetime.datetime.utcnow(),
                "produtos": data.get("cartItems", []) or [],
                "cliente_nome": f"{billing_data.get('firstName')} {billing_data.get('lastName')}",
                "nome_crianca": billing_data.get("fullNameChild", ""),
                "cliente_cpf": billing_data.get("cpf", ""),
                "cliente_email": billing_data.get('email', ''),
                "cliente_escola": billing_data.get('school', 'N/A'),
                "cliente_telefone": billing_data.get("phone", ""),
                "valor": float(payment_details['amount']),
                "status_cielo_codigo": response_json.get('Payment', {}).get('Status'),
                "status_cielo_mensagem": response_json.get('Payment', {}).get('ReturnMessage', 'Desconhecido'),
                "tipo_pagamento": "Débito (Crédito à Vista)",
                "bandeira": "Visa"
            }
            db.collection('vendas').document(str(pid)).set(venda_data)
            return jsonify({"status": "success", "cielo_response": response_json}), 200
        else:
            return jsonify({"status": "error", "cielo_error": response_json}), response.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/processar-pix', methods=['POST'])
def processar_pix():
    try:
        data = request.get_json()
        payment_details = data['paymentDetails']
        billing_data = data['billingData']
        merchant_order_id = f"LV_PIX_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"

        pix_data = {
            "MerchantOrderId": merchant_order_id,
            "Customer": {"Name": f"{billing_data.get('firstName')} {billing_data.get('lastName')}", "Identity": billing_data.get('cpf'), "Email": billing_data.get('email')},
            "Payment": {"Type": "Pix", "Amount": int(float(payment_details['amount']) * 100), "Pix": {"ExpiresIn": 3600}}
        }
        
        response = requests.post(CIELO_API_URL, headers={"Content-Type": "application/json", "MerchantId": MERCHANT_ID, "MerchantKey": MERCHANT_KEY}, data=json.dumps(pix_data))
        response_json = response.json()

        if response.status_code == 201:
            pid = response_json.get('Payment', {}).get('PaymentId')
            qr_code = response_json.get('Payment', {}).get('QrCodeString')
            if pid and qr_code:
                venda_data = {
                    "payment_id": pid,
                    "merchant_order_id": merchant_order_id,
                    "data_hora": datetime.datetime.utcnow(),
                    "produtos": data.get("cartItems", []) or [],
                    "cliente_nome": f"{billing_data.get('firstName')} {billing_data.get('lastName')}",
                    "nome_crianca": billing_data.get("fullNameChild", ""),
                    "cliente_cpf": billing_data.get("cpf", ""),
                    "cliente_email": billing_data.get('email', ''),
                    "cliente_escola": billing_data.get('school', 'N/A'),
                    "cliente_telefone": billing_data.get("phone", ""), 
                    "valor": float(payment_details['amount']),
                    "status_cielo_codigo": 12, 
                    "status_cielo_mensagem": "Aguardando Pagamento",
                    "status_interno": "Aguardando PIX",
                    "tipo_pagamento": "PIX",
                    "qr_code_string": qr_code,
                    "qr_code_image_url": response_json.get('Payment', {}).get('QrCodeImageUrl')
                }
                db.collection('vendas').document(str(pid)).set(venda_data)
                return jsonify({"status": "success", "cielo_response": response_json, "qr_code_string": qr_code, "qr_code_image_url": venda_data["qr_code_image_url"]}), 200
            else:
                return jsonify({"status": "error", "message": "Sem QR Code"}), 400
        else:
            return jsonify({"status": "error", "cielo_error": response_json}), response.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/processar-boleto', methods=['POST'])
def processar_boleto():
    try:
        data = request.get_json()
        payment_details = data['paymentDetails']
        billing_data = data['billingData']
        merchant_order_id = f"LV_BOLETO_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"
        due_date = (datetime.date.today() + datetime.timedelta(days=5)).strftime('%Y-%m-%d')

        boleto_data = {
            "MerchantOrderId": merchant_order_id,
            "Customer": {"Name": f"{billing_data.get('firstName')} {billing_data.get('lastName')}", "Identity": billing_data.get('cpf'), "Email": billing_data.get('email')},
            "Payment": {
                "Type": "Boleto", "Amount": int(float(payment_details['amount']) * 100), "Provider": "Bradesco", "ExpirationDate": due_date,
                "Instructions": "Não receber após o vencimento"
            }
        }
        response = requests.post(CIELO_API_URL, headers={"Content-Type": "application/json", "MerchantId": MERCHANT_ID, "MerchantKey": MERCHANT_KEY}, data=json.dumps(boleto_data))
        response_json = response.json()

        if response.status_code == 201:
            pid = response_json.get('Payment', {}).get('PaymentId')
            boleto_url = response_json.get('Payment', {}).get('Url')
            if pid and boleto_url:
                venda_data = {
                    "payment_id": pid,
                    "merchant_order_id": merchant_order_id,
                    "data_hora": datetime.datetime.utcnow(),
                    "produtos": data.get("cartItems", []) or [],
                    "cliente_nome": f"{billing_data.get('firstName')} {billing_data.get('lastName')}",
                    "nome_crianca": billing_data.get("fullNameChild", ""),
                    "cliente_cpf": billing_data.get("cpf", ""),
                    "cliente_email": billing_data.get('email', ''),
                    "cliente_escola": billing_data.get('school', 'N/A'),
                    "cliente_telefone": billing_data.get("phone", ""),
                    "valor": float(payment_details['amount']),
                    "status_cielo_codigo": 1,
                    "status_cielo_mensagem": "Aguardando Pagamento",
                    "status_interno": "Aguardando Boleto",
                    "tipo_pagamento": "Boleto",
                    "boleto_url": boleto_url,
                    "bar_code_number": response_json.get('Payment', {}).get('BarCodeNumber'),
                    "digitable_line": response_json.get('Payment', {}).get('DigitableLine')
                }
                db.collection('vendas').document(str(pid)).set(venda_data)
                return jsonify({"status": "success", "cielo_response": response_json, "boleto_url": boleto_url, "bar_code_number": venda_data["bar_code_number"], "digitable_line": venda_data["digitable_line"]}), 200
            else:
                return jsonify({"status": "error", "message": "Sem Boleto URL"}), 400
        else:
            return jsonify({"status": "error", "cielo_error": response_json}), response.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/registrar-venda', methods=['POST'])
def registrar_venda():
    try:
        data = request.get_json()
        order_id = data.get('orderId')
        items = data.get('items')
        total = data.get('total')
        billing_data = data.get('billingData')
        metodo = data.get('metodo')

        venda_data = {
            "payment_id": order_id,
            "merchant_order_id": order_id,
            "data_hora": datetime.datetime.utcnow(),
            "produtos": items,
            "cliente_nome": f"{billing_data.get('firstName')} {billing_data.get('lastName')}",
            "nome_crianca": billing_data.get('fullNameChild'),
            "cliente_cpf": billing_data.get('cpf'),
            "cliente_email": billing_data.get('email'),
            "cliente_escola": billing_data.get('school'),
            "cliente_telefone": billing_data.get('phone'),
            "valor": float(total),
            "status_cielo_codigo": 2,
            "status_cielo_mensagem": "Aprovado via Registro Direto",
            "tipo_pagamento": metodo,
            "recuperada": False
        }
        db.collection('vendas').document(str(order_id)).set(venda_data)
        
        try:
            num = re.sub(r'\D', '', str(billing_data.get('phone', '')))
            if num and not num.startswith("55"): num = "55" + num
            if num: enviar_whatsapp(num, gerar_mensagem_whatsapp(venda_data))
        except: pass

        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==============================================================================
#  ROTAS DE RELATÓRIOS E DASHBOARD (Com correções de quantidade e duplicidade)
# ==============================================================================

@app.route('/verificar-status/<payment_id>', methods=['GET'])
def verificar_status(payment_id):
    try:
        url = f"{CIELO_API_QUERY_URL}/{payment_id}"
        headers = {"Content-Type": "application/json", "MerchantId": MERCHANT_ID, "MerchantKey": MERCHANT_KEY}
        response = requests.get(url, headers=headers)
        data = response.json()
        status = data.get("Payment", {}).get("Status")

        if status == 2:
            venda_ref = db.collection("vendas").document(payment_id)
            doc = venda_ref.get()
            if doc.exists:
                venda_data = doc.to_dict()
                if venda_data.get('status_cielo_codigo') != 2:
                    venda_ref.update({"status_cielo_codigo": 2, "status_interno": "Pago"})

                if not venda_data.get("whatsapp_enviado"):
                    num = re.sub(r'\D', '', str(venda_data.get("cliente_telefone", "")))
                    if num and not num.startswith("55"): num = "55" + num
                    if num and len(num) > 10:
                        enviar_whatsapp(num, gerar_mensagem_whatsapp(venda_data))
                        venda_ref.update({"whatsapp_enviado": True})
        
        return jsonify({"status": status}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/vendas', methods=['GET'])
def get_vendas():
    try:
        if 'db' not in globals() or db is None: return jsonify({"error": "Banco indisponível"}), 500
        
        period = request.args.get('period', 'today') 
        school_filter = request.args.get('school')
        limit = int(request.args.get('limit', 500))
        offset = int(request.args.get('offset', 0))
        if limit > 1000: limit = 1000

        now_utc = datetime.datetime.utcnow() 
        start_date = None
        end_date = None

        if period == 'today':
            start_date = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now_utc.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif period == 'yesterday':
            yd = now_utc - datetime.timedelta(days=1)
            start_date = yd.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = yd.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif period == 'last7days':
            start_date = (now_utc - datetime.timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now_utc
        elif period == 'currentMonth':
            start_date = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0) # Corrigido fim do mes
            
        elif period == 'lastMonth':
             # Ajuste simples para last month
             first_this_month = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
             end_date = first_this_month - datetime.timedelta(microseconds=1)
             start_date = end_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        vendas_query = db.collection('vendas')
        if school_filter:
            vendas_query = vendas_query.where('cliente_escola', '==', requests.utils.unquote(school_filter).strip())
        if start_date:
            vendas_query = vendas_query.where('data_hora', '>=', start_date)
        if end_date:
            vendas_query = vendas_query.where('data_hora', '<=', end_date)
            
        vendas_query = vendas_query.order_by('data_hora', direction=firestore.Query.DESCENDING).offset(offset).limit(limit)
        docs = vendas_query.stream()
        
        lista = []
        ids_vistos = set()

        for doc in docs:
            v = doc.to_dict()
            pid = v.get('payment_id')
            if pid in ids_vistos: continue
            ids_vistos.add(pid)
            
            v['id'] = doc.id
            if isinstance(v.get('data_hora'), datetime.datetime):
                v['data_hora'] = v['data_hora'].isoformat()
            lista.append(v)
            
        return jsonify(lista), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/relatorios/escola/<string:nome_escola_url>', methods=['GET'])
def get_vendas_por_escola(nome_escola_url):
    try:
        nome_escola = requests.utils.unquote(nome_escola_url).strip()
        vendas_query = db.collection('vendas').where('cliente_escola', '==', nome_escola).order_by('data_hora', direction=firestore.Query.DESCENDING).stream()
        
        vendas_detalhadas = []
        ids_processados = set()

        for doc in vendas_query:
            venda = doc.to_dict()
            p_id = venda.get('payment_id')

            try: status = int(venda.get('status_cielo_codigo', 0))
            except: status = 0
            if status != 2: continue

            if p_id in ids_processados: continue
            ids_processados.add(p_id)

            produtos = venda.get('produtos', [])
            total_qtd = 0
            contagem = {}
            if isinstance(produtos, list):
                for p in produtos:
                    nm = p.get('name', 'N/A')
                    qt = int(p.get('quantity', 1))
                    total_qtd += qt
                    contagem[nm] = contagem.get(nm, 0) + qt
            
            produto_str = ", ".join([f"{q}x {n}" for n, q in contagem.items()])
            
            dt_iso = 'N/A'
            if isinstance(venda.get('data_hora'), datetime.datetime):
                dt_iso = venda['data_hora'].isoformat()

            vendas_detalhadas.append({
                'aluno': venda.get('nome_crianca') or venda.get('cliente_nome', 'N/A'),
                'escola': venda.get('cliente_escola', 'N/A'),
                'produto': produto_str,
                'quantidade': total_qtd,
                'valor': float(venda.get('valor', 0)),
                'data_compra': dt_iso,
                'payment_id': p_id
            })
        
        return jsonify(vendas_detalhadas), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/relatorios/escola/exportar_xlsx/<string:nome_escola_url>', methods=['GET'])
def exportar_alunos_xlsx(nome_escola_url):
    try:
        nome_escola = requests.utils.unquote(nome_escola_url).strip()
        vendas_query = db.collection('vendas').where('cliente_escola', '==', nome_escola).order_by('data_hora', direction=firestore.Query.DESCENDING).stream()
        
        vendas_export = []
        ids_processados = set()

        for doc in vendas_query:
            venda = doc.to_dict()
            p_id = venda.get('payment_id')

            try: status = int(venda.get('status_cielo_codigo', 0))
            except: status = 0
            if status != 2: continue

            if p_id in ids_processados: continue
            ids_processados.add(p_id)

            produtos = venda.get('produtos', [])
            total_qtd = 0
            contagem = {}
            if isinstance(produtos, list):
                for p in produtos:
                    nm = p.get('name', 'N/A')
                    qt = int(p.get('quantity', 1))
                    total_qtd += qt
                    contagem[nm] = contagem.get(nm, 0) + qt
            
            produto_str = ", ".join([f"{q}x {n}" for n, q in contagem.items()])

            vendas_export.append({
                'aluno': venda.get('nome_crianca') or venda.get('cliente_nome', 'N/A'),
                'escola': venda.get('cliente_escola', 'N/A'),
                'produto': produto_str,
                'quantidade': total_qtd,
                'valor': float(venda.get('valor', 0)),
                'data_compra': venda.get('data_hora')
            })

        if not vendas_export: return jsonify({"error": "Sem dados"}), 404

        wb = Workbook()
        ws = wb.active
        ws.title = "Vendas"
        # Headers corrigidos
        headers = ["Aluno", "Escola", "Produto", "Qtd (Unidades)", "Valor (R$)", "Data Compra"]
        ws.append(headers)

        for row_data in vendas_export:
            dt = row_data['data_compra']
            if isinstance(dt, datetime.datetime): dt = dt.replace(tzinfo=None)
            else: dt = "N/A"
            
            ws.append([
                row_data['aluno'], row_data['escola'], row_data['produto'], 
                row_data['quantidade'], row_data['valor'], dt
            ])
        
        for row in ws.iter_rows(min_row=2):
            row[4].number_format = '"R$"#,##0.00'
            row[5].number_format = 'DD/MM/YYYY HH:MM:SS'
        
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 25

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', download_name=f"relatorio_{nome_escola}.xlsx", as_attachment=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/relatorios/receita-por-produto', methods=['GET'])
def get_receita_por_produto():
    try:
        if 'db' not in globals() or db is None: return jsonify({"error": "Banco indisponível"}), 500

        vendas_ref = db.collection('vendas').order_by('data_hora', direction=firestore.Query.DESCENDING).limit(1000)
        docs = vendas_ref.select(['produtos', 'valor', 'status_cielo_codigo']).stream()
        produtos_data = defaultdict(lambda: {'quantidade': 0, 'receita': 0.0})

        for doc in docs:
            venda = doc.to_dict()
            if venda.get('status_cielo_codigo') in [2, 12, 1]: 
                produtos = venda.get('produtos', [])
                if not isinstance(produtos, list): continue
                for p in produtos:
                    nome = p.get('name', 'Produto Desconhecido')
                    try: valor = float(p.get('price', venda.get('valor', 0)))
                    except: valor = 0.0
                    produtos_data[nome]['quantidade'] += 1
                    produtos_data[nome]['receita'] += valor

        lista = [{'nome': n, 'quantidade_vendida': d['quantidade'], 'receita_gerada': d['receita']} for n, d in produtos_data.items()]
        lista.sort(key=lambda x: x['receita_gerada'], reverse=True)
        return jsonify(lista), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/relatorios/escolas', methods=['GET'])
def get_relatorio_escolas():
    try:
        if 'db' not in globals() or db is None: return jsonify({"error": "Banco indisponível"}), 500
        vendas_ref = db.collection('vendas').order_by('data_hora', direction=firestore.Query.DESCENDING).limit(1000)
        docs = vendas_ref.select(['cliente_escola', 'valor', 'status_cielo_codigo', 'produtos', 'cliente_email']).stream()
        escolas_resumo = defaultdict(lambda: {'total_vendas': 0, 'receita_total': 0.0, 'produtos_vendidos': defaultdict(int), 'alunos': set()})
        
        for doc in docs:
            venda = doc.to_dict()
            if venda.get('status_cielo_codigo') == 2:
                esc = venda.get('cliente_escola', 'Escola Desconhecida')
                val = float(venda.get('valor', 0))
                escolas_resumo[esc]['total_vendas'] += 1
                escolas_resumo[esc]['receita_total'] += val
                escolas_resumo[esc]['alunos'].add(venda.get('cliente_email'))
                prods = venda.get('produtos', [])
                if prods: escolas_resumo[esc]['produtos_vendidos'][prods[0].get('name')] += 1

        resumo = []
        for nome, d in escolas_resumo.items():
            mv = max(d['produtos_vendidos'], key=d['produtos_vendidos'].get) if d['produtos_vendidos'] else 'N/A'
            resumo.append({'nome_escola': nome, 'total_vendas': d['total_vendas'], 'receita_total': d['receita_total'], 'produto_mais_vendido': mv, 'quantidade_alunos_compraram': len(d['alunos'])})
        
        resumo.sort(key=lambda x: x['receita_total'], reverse=True)
        return jsonify(resumo), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/financeiro/resumo', methods=['GET'])
def get_financeiro_resumo():
    try:
        if 'db' not in globals() or db is None: return jsonify({"error": "Banco indisponível"}), 500
        
        period = request.args.get('period', 'allTime')
        # Lógica de datas para filtro
        now_utc = datetime.datetime.utcnow() 
        start_date = None
        end_date = None

        if period == 'today':
            start_date = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now_utc.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif period == 'yesterday':
            yd = now_utc - datetime.timedelta(days=1)
            start_date = yd.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = yd.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif period == 'last7days':
            start_date = (now_utc - datetime.timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now_utc
        elif period == 'currentMonth':
            start_date = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = now_utc
        elif period == 'lastMonth':
             first_this_month = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
             end_date = first_this_month - datetime.timedelta(microseconds=1)
             start_date = end_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        vendas_query = db.collection('vendas')
        if start_date: vendas_query = vendas_query.where('data_hora', '>=', start_date)
        if end_date: vendas_query = vendas_query.where('data_hora', '<=', end_date)

        docs = vendas_query.select(['valor', 'status_cielo_codigo', 'produtos', 'tipo_pagamento']).stream()

        valor_ganho = 0.0
        valor_reembolsado = 0.0
        quantidade_vendas = 0
        metodos = defaultdict(float)

        for doc in docs:
            venda = doc.to_dict()
            status = venda.get('status_cielo_codigo')
            try: val = float(venda.get('valor', 0))
            except: val = 0.0
            tipo = venda.get('tipo_pagamento', 'Outro')

            if status in [2, 12, 1]:
                valor_ganho += val
                metodos[tipo] += val
                # Soma quantidade de livros
                prods = venda.get('produtos', [])
                if isinstance(prods, list) and len(prods) > 0:
                    quantidade_vendas += sum([int(p.get('quantity', 1)) for p in prods])
                else:
                    quantidade_vendas += 1
            elif status == 3:
                valor_reembolsado += val
        
        metodos_percent = {m: {'valor': v, 'percentual': (v/valor_ganho) if valor_ganho > 0 else 0} for m, v in metodos.items()}

        return jsonify({
            'valor_ganho': valor_ganho,
            'valor_reembolsado': valor_reembolsado,
            'quantidade_vendas': quantidade_vendas,
            'metodos_pagamento': metodos_percent
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/sincronizar-pendentes', methods=['GET'])
def sincronizar_pendentes():
    try:
        vendas_pendentes = db.collection('vendas').where('status_cielo_codigo', 'in', [1, 12]).stream()
        atualizadas = 0
        headers = {"Content-Type": "application/json", "MerchantId": MERCHANT_ID, "MerchantKey": MERCHANT_KEY}

        for doc in vendas_pendentes:
            venda = doc.to_dict()
            payment_id = venda.get('payment_id')
            if not payment_id: continue

            try:
                resp = requests.get(f"{CIELO_API_QUERY_URL}/{payment_id}", headers=headers)
                if resp.status_code == 200:
                    status_real = resp.json().get('Payment', {}).get('Status')
                    if status_real == 2:
                        db.collection('vendas').document(doc.id).update({
                            "status_cielo_codigo": 2, "status_cielo_mensagem": "Pago", "status_interno": "Sincronizado"
                        })
                        atualizadas += 1
            except: pass
        
        return jsonify({"message": "Sincronização concluída", "recuperadas": atualizadas}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
"""
@app.route("/emitir-nfe", methods=["POST"])
def emitir_nfe():
    try:
        data = request.get_json()
        payment_id = data.get("payment_id")
        if not payment_id: return jsonify({"error": "payment_id não informado"}), 400

        vendas = db.collection("vendas").where("payment_id", "==", payment_id).stream()
        venda_doc = None
        venda_ref = None
        for v in vendas:
            venda_doc = v.to_dict()
            venda_ref = v.reference
            break

        if not venda_doc: return jsonify({"error": "Venda não encontrada"}), 404
        if venda_doc.get("status_cielo_codigo") != 2: return jsonify({"error": "NF-e só pode ser emitida para pagamento aprovado"}), 400

        venda = {"cliente_nome": venda_doc["cliente_nome"], "cliente_cpf": venda_doc["cliente_cpf"]}
        itens = venda_doc.get("produtos", [])
        
        # Recupera número sequencial
        serie, numero = obter_proximo_numero_nfe()

        xml = gerar_xml_nfe(venda=venda, itens=itens, ambiente="2", serie=serie, numero_nfe=str(numero))
        xml_assinado = assinar_xml_nfe(xml)
        retorno = enviar_nfe_sefaz(xml_assinado, ambiente="2")

        if retorno.get("status") == "autorizada":
            venda_ref.update({
                "nfe_emitida": True, "nfe_xml": retorno["xml_autorizado"], 
                "nfe_chave": retorno.get("chNFe"), "nfe_emitida_em": datetime.datetime.utcnow()
            })

        return jsonify(retorno), 200
    except Exception as e:
        return jsonify({"error": "Erro ao emitir NF-e", "detalhes": str(e)}), 500
"""
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)