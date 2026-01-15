import json
import boto3
from statistics import mean
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
table = dynamodb.Table("GlucoseData")
ses = boto3.client("ses", region_name="eu-west-1")


N_MUESTRAS = 10
UMBRAL_HIPERGLUCEMIA = 180
UMBRAL_HIPOGLUCEMIA = 70
EMAIL_DESTINO = "lorenzorodriguezmarina@gmail.com"
EMAIL_ORIGEN = "alertaglucosa@gmail.com"

INTERVALO_MIN = 5
MAX_DELTA_15_MIN = 40
DELTA_MAX = MAX_DELTA_15_MIN * (INTERVALO_MIN / 15)


# generar gráfica SVG

def generar_svg(glucosas, medias, picos_inestables, glucosa_predicha, timestamps):
    width = 850
    height = 450 
    padding_left = 80
    padding_bottom = 80
    padding_top = 40
    padding_right = 100 
    
    min_g = 40
    max_g = 250
    rango = max_g - min_g

    def y_coord(g):
        return height - padding_bottom - ((g - min_g) / rango) * (height - padding_top - padding_bottom)

    def x_coord(i):
        return padding_left + i * (width - padding_left - padding_right) / len(glucosas)

    puntos = []
    circles = []
    labels = []

    for i, g in enumerate(glucosas):
        x = x_coord(i)
        y = y_coord(g)
        puntos.append(f"{x},{y}")
        circles.append(f'<circle cx="{x}" cy="{y}" r="4" fill="#1f77b4"/>')

    polyline = " ".join(puntos)
    circles_picos = []
    lines_rango = []
    small_lines = []

    for i, g_pico in enumerate(picos_inestables):
        media = medias[i]
        x = x_coord(i)
        y_pico = y_coord(g_pico)
        circles_picos.append(f'<circle cx="{x}" cy="{y_pico}" r="4" fill="red"/>')
        extremo = 2 * media - g_pico
        y_extremo = y_coord(extremo)
        lines_rango.append(f'<line x1="{x}" y1="{y_pico}" x2="{x}" y2="{y_extremo}" stroke="orange" stroke-width="2" stroke-dasharray="4,2"/>')
        small_lines.append(f'<line x1="{x-5}" y1="{y_extremo}" x2="{x+5}" y2="{y_extremo}" stroke="orange" stroke-width="2"/>')
        labels.append(f'<text x="{x+5}" y="{y_coord(media)-10}" font-size="11" fill="blue">{media:.0f}</text>')

    x_labels = []
    for i, ts in enumerate(timestamps):
        if ts > 10**12: ts = ts / 1000 
        x = x_coord(i)
        dt = datetime.fromtimestamp(ts)
        label = dt.strftime("%d/%m %H:%M")
        x_labels.append(f'<text x="{x}" y="{height - padding_bottom + 25}" font-size="10" text-anchor="end" transform="rotate(-45 {x},{height - padding_bottom + 25})">{label}</text>')

    x_last = x_coord(len(glucosas) - 1)
    x_pred = x_coord(len(glucosas))
    y_last = y_coord(glucosas[-1])
    y_pred = y_coord(glucosa_predicha)

    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">
    <rect width="100%" height="100%" fill="white"/>
    <line x1="{padding_left}" y1="{y_coord(70)}" x2="{width-padding_right}" y2="{y_coord(70)}" stroke="red" stroke-dasharray="5,5"/><text x="10" y="{y_coord(70)+4}" font-size="12" fill="red">70 (Hipo)</text>
    <line x1="{padding_left}" y1="{y_coord(180)}" x2="{width-padding_right}" y2="{y_coord(180)}" stroke="red" stroke-dasharray="5,5"/><text x="10" y="{y_coord(180)+4}" font-size="12" fill="red">180 (Hiper)</text>
    <line x1="{padding_left}" y1="{padding_top}" x2="{padding_left}" y2="{height-padding_bottom}" stroke="black" stroke-width="2"/>
    <line x1="{padding_left}" y1="{height-padding_bottom}" x2="{width-padding_right}" y2="{height-padding_bottom}" stroke="black" stroke-width="2"/>
    <text x="{ (width + padding_left - padding_right) / 2 }" y="{height - 10}" font-size="14" font-weight="bold" text-anchor="middle">Hora (Día/Mes)</text>
    <text x="20" y="{ (height) / 2 }" font-size="14" font-weight="bold" transform="rotate(-90 20,{(height)/2})" text-anchor="middle">Glucosa (mg/dL)</text>
    {''.join(x_labels)}
    <polyline points="{polyline}" fill="none" stroke="#1f77b4" stroke-width="3"/>
    {''.join(circles)}{''.join(labels)}{''.join(circles_picos)}{''.join(lines_rango)}{''.join(small_lines)}
    <line x1="{x_last}" y1="{y_last}" x2="{x_pred}" y2="{y_pred}" stroke="purple" stroke-width="2" stroke-dasharray="4,2"/>
    <circle cx="{x_pred}" cy="{y_pred}" r="5" fill="purple"/>
    <text x="{x_pred + 8}" y="{y_pred - 5}" font-size="12" fill="purple" font-weight="bold">Predicción</text>
    <text x="{x_pred + 8}" y="{y_pred + 10}" font-size="12" fill="purple" font-weight="bold">{glucosa_predicha:.2f} mg/dL</text>
    </svg>"""
    return svg


def lambda_handler(event, context):
    try:
        record = event["Records"][0]
        device_id = record["dynamodb"]["Keys"]["deviceId"]["S"]
    except Exception as e:
        print(f"Error parseando evento: {e}")
        return {"statusCode": 200, "body": "Error en datos de entrada"}

    response = table.query(
        KeyConditionExpression=Key("deviceId").eq(device_id),
        ScanIndexForward=False,
        Limit=N_MUESTRAS
    )
    items = response.get("Items", [])
    if not items:
        return {"statusCode": 200, "body": "Sin historial"}

    items.sort(key=lambda x: int(x["timestamp"]))

    # Extracción datos
    timestamps = [int(i["timestamp"]) for i in items]
    glucosas = [float(i.get("glucosa", 0)) for i in items]
    variaciones = [float(i.get("variacion_glucosa", 0)) for i in items]
    tendencias = [int(i.get("tendencia", 0)) for i in items]
    bpm_list = [int(i.get('bpm', 0)) for i in items]
    oxygen_list = [int(i.get('oxygen', 0)) for i in items]

    # Predicción 
    pendiente = (glucosas[-1] - glucosas[0]) / len(glucosas) if len(glucosas) > 1 else 0
    delta = max(-DELTA_MAX, min(pendiente, DELTA_MAX))
    glucosa_predicha = glucosas[-1] + delta

    bpm_actual = bpm_list[-1]
    oxigeno_actual = oxygen_list[-1]
    media_actual = glucosas[-1] 


    en_reposo = bpm_actual < 80 and oxigeno_actual > 95
    agitado = bpm_actual > 110 or oxigeno_actual < 92

    alerta = None
    recomendaciones = []

    print(f"Glucosa: {media_actual}, Glucosa Predicha: {glucosa_predicha}, Oxigeno: {oxigeno_actual}, BPM: {bpm_actual}, En reposo: {en_reposo}, Agitado: {agitado}")
    if media_actual >= UMBRAL_HIPERGLUCEMIA:
        alerta = "⚠️ HIPERGLUCEMIA"
        if en_reposo:
            recomendaciones.append("Nivel alto de glucosa mientras el estado es en reposo: Considera una caminata ligera para ayudar a bajar el nivel.")
        else:
            recomendaciones.append("Nivel alto en actividad: Mantén hidratación y vigila si la tendencia sube.")
        recomendaciones.append("Bebe agua abundante.")

    elif media_actual <= UMBRAL_HIPOGLUCEMIA:
        alerta = "⚠️ HIPOGLUCEMIA"
        recomendaciones.append("Ingiere 15g de carbohidratos rápidos (zumo o azúcar).")
        if agitado:
            recomendaciones.append("¡PELIGRO! Estás agitado. Detén toda actividad física de inmediato.")
        else:
            recomendaciones.append("Permanece en reposo hasta normalizar niveles.")

 
    ultimo_item = items[-1]

    if int(ultimo_item.get('estado_sensor',0)) == 1:
         recomendaciones.append("Aviso: Lecturas inestables del sensor detectadas.")

    if int(ultimo_item.get('taquicardia', 0)) == 1:
        recomendaciones.append("Se ha detectado algún evento de taquicardia en los últimos 15 minutos.")

    if int(ultimo_item.get('bradicardia', 0)) == 1:
        recomendaciones.append("Se ha detectado algún evento de bradicardia en los últimos 15 minutos.")

    if int(ultimo_item.get('hipoxia', 0)) == 1:
        recomendaciones.append("Se ha detectado algún evento de hipoxia en los últimos 15 minutos.")


    if not alerta:
        return {"statusCode": 200, "body": "Niveles normales"}

    # Generar SVG y Email
    picos_inestables = [round(g + v, 2) if t == 1 else round(g - v, 2) for g, v, t in zip(glucosas, variaciones, tendencias)]
    svg = generar_svg(glucosas, glucosas, picos_inestables, glucosa_predicha, timestamps)
    
    msg = MIMEMultipart()
    msg["Subject"] = f"{alerta} - {media_actual:.0f} mg/dL"
    msg["From"] = EMAIL_ORIGEN
    msg["To"] = EMAIL_DESTINO

    html = f"""
    <html><body>
    <h2 style="color: red;">{alerta}</h2>
    <p><b>Dispositivo:</b> {device_id}</p>
    <p><b>Glucosa Actual:</b> {media_actual:.2f} mg/dL</p>
    <p><b>Predicción (15 min):</b> {glucosa_predicha:.2f} mg/dL</p>
    <p>Frecuencia Cardíaca:{bpm_actual} BPM</p>
    <p>Saturación Oxígeno:{oxigeno_actual}%</p>
    <hr>
    <p><b>Recomendaciones:</b><br>{'<br>'.join(recomendaciones)}</p>
    <hr>
    <p>Gráfica de tendencia de las últimas muestras:</p>
    </body></html>
    """
    msg.attach(MIMEText(html, "html"))

    part = MIMEBase("image", "svg+xml")
    part.set_payload(svg.encode("utf-8"))
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", 'attachment; filename="grafica.svg"')
    msg.attach(part)

    try:
        ses.send_raw_email(Source=EMAIL_ORIGEN, Destinations=[EMAIL_DESTINO], RawMessage={"Data": msg.as_string()})
        return {"statusCode": 200, "body": "Alerta enviada"}
    except Exception as e:
        print(f"Error SES: {e}")
        return {"statusCode": 500, "body": "Error SES"}
