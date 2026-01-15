import json
import boto3
from datetime import datetime
from decimal import Decimal

dynamodb = boto3.resource('dynamodb', region_name='eu-west-1') 
table = dynamodb.Table('GlucoseData')

def lambda_handler(event, context):
    # imprimir evento
    print("Evento recibido (crudo):")
    print(event)


    body = event.get("body")
    if body:
        try:
            data = json.loads(body)
        except Exception as e:
            print("Error parseando JSON:", e)
            return {"statusCode": 200, "body": "OK"}
    else:
        data = event

    device_id = data.get("deviceId", "unknown")

    try:
        # Convertir a Decimal para poder realizar las operaciones algebraicas correctamente
        glucose_raw = Decimal(str(data.get("glucosa_raw", 0)))/100
        max_var = Decimal (str(data.get("max_var", 0)))/100
        
        bpm = int(data.get("bpm", 0))
        oxygen = int(data.get("oxygen", 0))
        flags = int(data.get("flags", 0))

        signo          = (flags >> 0) & 1
        bradicardia    = (flags >> 1) & 1
        taquicardia    = (flags >> 2) & 1
        hipoxia        = (flags >> 3) & 1
        estado_sensor  = (flags >> 4) & 1

    except (ValueError, TypeError) as e:
        print("ERROR: Valores no válidos:", e)
        return {"statusCode": 200, "body": "OK"} 
      
    timestamp = int(data.get("time", datetime.utcnow().timestamp()))


    try:
        table.put_item(
            Item={
                'deviceId': device_id,
                'timestamp': timestamp,
                'glucosa': glucose_raw,
                'variacion_glucosa': max_var,
                'bpm': bpm,
                'oxygen':oxygen,
                'signo': signo,
                'bradicardia': bradicardia,
                'taquicardia': taquicardia,
                'hipoxia': hipoxia,
                'estado_sensor': estado_sensor,
               
            }
        )

        response = table.get_item(
            Key={
                'deviceId': device_id,
                'timestamp': timestamp
            }
        )
        print("Item guardado:", response.get("Item")) 
        print(f"Guardado en DynamoDB: {device_id} - {glucose_raw} - {max_var} - {bpm} - {oxygen}")

    except Exception as e:
        print("Error guardando en DynamoDB:", e)

    return {"statusCode": 200, "body": "OK"}
