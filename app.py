from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import requests
import json
import os
import datetime
import traceback
from dotenv import load_dotenv
from collections import defaultdict 
import io # Para a exportação XLSX
from openpyxl import Workbook # Para a exportação XLSX
from openpyxl.styles import Font, Alignment # Para estilos em XLSX
import sys # Para sys.exit caso a inicialização do Firebase falhe criticamente

# Carrega as variáveis de ambiente do arquivo .env (se existir)
load_dotenv()

app = Flask(__name__)
# Habilita CORS para todas as rotas da sua aplicação Flask
CORS(app, resources={r"/*": {"origins": [
    "http://localhost:5500", # Sua máquina (Live Server)
    "http://127.0.0.1:5500", # Sua máquina (Live Server)
    "http://127.0.0.1:5501", # Porta padrão do Flask se rodar sem Live Server
    "http://127.0.0.1:5503", # Adicionada a nova porta do Live Server (verifique qual porta o Live Server usa)
    "file://",               # Para arquivos HTML abertos diretamente do disco
    "null"                    # Outra origem para arquivos abertos diretamente do disco (Chrome)
]}})

# Suas credenciais e URLs da Cielo
MERCHANT_ID = os.getenv("CIELO_MERCHANT_ID")
MERCHANT_KEY = os.getenv("CIELO_MERCHANT_KEY")

# URLs da API Cielo (Ajuste para SANDBOX ou PRODUÇÃO conforme suas credenciais)
CIELO_API_URL = os.getenv("CIELO_API_URL_PROD", "https://api.cieloecommerce.cielo.com.br/1/sales/")
CIELO_API_QUERY_URL = os.getenv("CIELO_API_QUERY_URL_PROD", "https://apiquery.cieloecommerce.cielo.com.br/1/sales/")

# --- Importações e Inicialização do Firebase ---
# AS IMPORTAÇÕES PRECISAM VIR ANTES DA TENTATIVA DE USAR OS MÓDULOS
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

try:
    # Use firebase_admin._apps para evitar inicializar o aplicativo várias vezes em ambientes como Flask debug
    if not firebase_admin._apps:
        cred = credentials.Certificate('chave-firebase.json') # Garanta que este caminho está correto
        firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase inicializado com sucesso!")
except Exception as e:
    print(f"Erro ao inicializar Firebase: {e}")
    # Você pode considerar adicionar um sys.exit(1) aqui se a conexão com o Firebase for crítica
# --- Fim da Inicialização do Firebase ---


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
                    "produto": data['cartItems'][0]['name'] if data.get('cartItems') else 'N/A', # Assumindo um único produto
                    "cliente_nome": f"{billing_data.get('firstName', '')} {billing_data.get('lastName', '')}",
                    "cliente_email": billing_data.get('email', ''),
                    "cliente_escola": billing_data.get('school', 'N/A'), 
                    "valor": float(payment_details['amount']),
                    "status_cielo_codigo": response_json.get('Payment', {}).get('Status'),
                    "status_cielo_mensagem": response_json.get('Payment', {}).get('ReturnMessage', 'Status desconhecido'),
                    "tipo_pagamento": "Cartão de Crédito",
                    "bandeira": payment_details.get('brand', 'Visa')
                }
                db.collection('vendas').document().set(venda_data)
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
        if 'cardNumber' not in payment_details or 'holder' not in payment_details or \
           'expirationDate' not in payment_details or 'securityCode' not in payment_details or \
           'amount' not in payment_details:
            return jsonify({"error": "Dados de pagamento do cartão de débito incompletos"}), 400

        card_number = payment_details['cardNumber'].replace(" ", "").replace("-", "")
        amount_in_cents = int(float(payment_details['amount']) * 100)
        merchant_order_id = f"LV_DEBITO_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(4).hex()}"

        expiration_date_frontend_raw_debito = payment_details['expirationDate']
        cielo_expiration_date_debito = ""
        temp_numeric_date_debito = expiration_date_frontend_raw_debito.replace('/', '').replace(' ', '')
        month_debito = temp_numeric_date_debito[:2]
        year_part_debito = temp_numeric_date_debito[2:]
        try:
            if len(year_part_debito) == 2:
                full_year_int_debito = 2000 + int(year_part_debito)
                full_year_int_debito = max(full_year_int_debito, datetime.datetime.now().year) 
            elif len(year_part_debito) == 4:
                full_year_int_debito = int(year_part_debito)
            else:
                full_year_int_debito = 0
            cielo_expiration_date_debito = f"{month_debito}/{full_year_int_debito}"
        except ValueError:
            cielo_expiration_date_debito = expiration_date_frontend_raw_debito
        
        debit_data = {
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
                "Type": "DebitCard",
                "Amount": amount_in_cents,
                "Authenticate": True,
                "ReturnUrl": "http://localhost:5500/Front%20end/finalizar.html", # URL de retorno após 3DS
                "SoftDescriptor": "LIVRARIAWEB",
                "DebitCard": {
                    "CardNumber": card_number,
                    "Holder": payment_details['holder'],
                    "ExpirationDate": cielo_expiration_date_debito,
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

        response = requests.post(CIELO_API_URL, headers=headers, data=json.dumps(debit_data))
        response_json = response.json()

        if response.status_code == 201:
            if 'db' in globals() and db is not None:
                venda_data = {
                    "payment_id": response_json.get('Payment', {}).get('PaymentId'),
                    "merchant_order_id": merchant_order_id,
                    "data_hora": datetime.datetime.utcnow(), 
                    "produto": data['cartItems'][0]['name'] if data.get('cartItems') else 'N/A', # Assumindo um único produto
                    "cliente_nome": f"{billing_data.get('firstName', '')} {billing_data.get('lastName', '')}",
                    "cliente_email": billing_data.get('email', ''),
                    "cliente_escola": billing_data.get('school', 'N/A'), 
                    "valor": float(payment_details['amount']),
                    "status_cielo_codigo": response_json.get('Payment', {}).get('Status'),
                    "status_cielo_mensagem": response_json.get('Payment', {}).get('ReturnMessage', 'Status desconhecido'),
                    "status_interno": "Aguardando Autenticação",
                    "tipo_pagamento": "Cartão de Débito",
                    "bandeira": payment_details.get('brand', 'Visa'),
                    "redirect_url": response_json.get('Payment', {}).get('AuthenticationUrl')
                }
                db.collection('vendas').document().set(venda_data)
            return jsonify({"status": "success", "message": "Redirecionando para autenticação 3D Secure...", "cielo_response": response_json, "redirect_url": response_json['Payment']['AuthenticationUrl']}), 200
        else:
            return jsonify({"status": "error", "message": "Erro ao processar débito na Cielo", "cielo_error": response_json}), response.status_code

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
                        "produto": data['cartItems'][0]['name'] if data.get('cartItems') else 'N/A', # Assumindo um único produto
                        "cliente_nome": f"{billing_data.get('firstName', '')} {billing_data.get('lastName', '')}",
                        "cliente_email": billing_data.get('email', ''),
                        "cliente_escola": billing_data.get('school', 'N/A'), 
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
                        "produto": data['cartItems'][0]['name'] if data.get('cartItems') else 'N/A', # Assumindo um único produto
                        "cliente_nome": f"{billing_data.get('firstName', '')} {billing_data.get('lastName', '')}",
                        "cliente_email": billing_data.get('email', ''),
                        "cliente_escola": billing_data.get('school', 'N/A'), 
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

# ROTA PARA OBTER VENDAS GERAIS (USADA NO DASHBOARD E MINHAS VENDAS)
@app.route('/vendas', methods=['GET'])
def get_vendas():
    try:
        if 'db' not in globals() or db is None:
            return jsonify({"error": "Serviço de banco de dados indisponível."}), 500

        period = request.args.get('period', 'today') 
        now_utc = datetime.datetime.utcnow() 
        start_date = None
        end_date = None

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
        elif period == 'allTime':
            start_date = None
            end_date = None
        else:
            start_date = None 
            end_date = None 

        vendas_query = db.collection('vendas')
        if start_date:
            vendas_query = vendas_query.where('data_hora', '>=', start_date)
        if end_date:
            vendas_query = vendas_query.where('data_hora', '<=', end_date)
            
        docs = vendas_query.stream()
        lista_vendas = []
        for doc in docs:
            venda = doc.to_dict()
            venda['id'] = doc.id
            if isinstance(venda.get('data_hora'), datetime.datetime):
                venda['data_hora'] = venda['data_hora'].isoformat() 
            else:
                venda['data_hora'] = None 
            lista_vendas.append(venda)
        
        lista_vendas.sort(key=lambda x: x.get('data_hora', '0000-01-01T00:00:00') if x.get('data_hora') else '', reverse=True)
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
                produto_nome = venda.get('produto', 'Produto Desconhecido')
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
            produto_nome = venda.get('produto', 'Produto Desconhecido')
            valor = float(venda.get('valor', 0))

            if venda.get('status_cielo_codigo') in [2, 12, 1]: 
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
        for doc in vendas_query:
            venda = doc.to_dict()
            venda['id'] = doc.id 
            
            data_compra_iso = None
            if isinstance(venda.get('data_hora'), datetime.datetime):
                data_compra_iso = venda['data_hora'].isoformat()
            else:
                data_compra_iso = 'N/A' 

            vendas_detalhadas.append({
                'aluno': venda.get('cliente_nome', 'N/A'),
                'escola': venda.get('cliente_escola', 'N/A'),
                'produto': venda.get('produto', 'N/A'),
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

        vendas_query = db.collection('vendas').where('cliente_escola', '==', nome_escola) \
                         .order_by('data_hora', direction=firestore.Query.DESCENDING).stream()
        
        vendas_detalhadas_para_export = []
        for doc in vendas_query:
            venda = doc.to_dict()
            
            data_compra_excel = venda.get('data_hora') 
            
            # --- CORREÇÃO: Converte datetime aware para naive antes de exportar para Excel ---
            if isinstance(data_compra_excel, datetime.datetime):
                if data_compra_excel.tzinfo is not None and data_compra_excel.tzinfo.utcoffset(data_compra_excel) is not None:
                    data_compra_excel = data_compra_excel.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            else:
                data_compra_excel = str(data_compra_excel) 

            valor_excel = float(venda.get('valor', 0))

            vendas_detalhadas_para_export.append({
                'aluno': venda.get('cliente_nome', 'N/A'),
                'escola': venda.get('cliente_escola', 'N/A'),
                'produto': venda.get('produto', 'N/A'),
                'valor': valor_excel,
                'data_compra': data_compra_excel 
            })
        
        if not vendas_detalhadas_para_export:
            return jsonify({"message": "Nenhum dado para exportar para esta escola."}), 404

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
        
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter 
            for cell in col:
                try: 
                    if cell.value is not None:
                        cell_value_str = str(cell.value)
                        
                        if column == 'D': # Coluna Valor (formato de moeda)
                           cell.number_format = '"R$"#,##0.00' 
                        elif column == 'E' and isinstance(cell.value, datetime.datetime): # Coluna Data Compra (formato de data/hora)
                           cell.number_format = 'DD/MM/YYYY HH:MM:SS' 
                           cell_value_str = cell.value.strftime('%Y-%m-%d %H:%M:%S') # Para cálculo de largura
                        
                        if len(cell_value_str) > max_length:
                            max_length = len(cell_value_str)
                except Exception as cell_err:
                    print(f"Erro ao processar célula para largura automática: {cell.value} - {cell_err}")
                    pass
            adjusted_width = (max_length + 2) 
            ws.column_dimensions[column].width = min(adjusted_width, 70) 

        ws.auto_filter.ref = ws.dimensions

        output = io.BytesIO()
        wb.save(output)
        output.seek(0) 

        filename = f"relatorio_alunos_{nome_escola.replace(' ', '_')}_{datetime.date.today().strftime('%Y%m%d')}.xlsx"
        
        print(f"Exportação XLSX para '{nome_escola}' concluída e pronta para download.")
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

# ... (ESTA LINHA ABAIXO É ONDE SEU `if __name__ == '__main__':` DEVE ESTAR) ...
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
