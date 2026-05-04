"""
main.py
-------
API REST con FastAPI para exponer el scraper de MParkingSelfcare.
Incluye renovacion automatica de estacionamientos con APScheduler.
"""

import asyncio
import sys
import pytz
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import Optional
import uuid

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from scraper import MParkingScraper

ARG_TZ = pytz.timezone("America/Argentina/Buenos_Aires")

scheduler = AsyncIOScheduler(timezone=ARG_TZ)

auto_renovacion = {
    "activo": False,
    "usuario": None,
    "password": None,
    "valor_calle": None,
    "valor_altura": None,
    "patente": None,
    "job_id": None,
}


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, dict] = {}

    def crear(self, scraper: MParkingScraper) -> str:
        token = str(uuid.uuid4())
        self._sessions[token] = {
            "scraper": scraper,
            "creado_en": datetime.utcnow(),
            "ultimo_uso": datetime.utcnow(),
        }
        return token

    def obtener(self, token: str) -> Optional[MParkingScraper]:
        session = self._sessions.get(token)
        if not session:
            return None
        if datetime.utcnow() - session["ultimo_uso"] > timedelta(minutes=3):
            asyncio.create_task(self._cerrar_sesion(token))
            return None
        session["ultimo_uso"] = datetime.utcnow()
        return session["scraper"]

    async def cerrar(self, token: str):
        session = self._sessions.pop(token, None)
        if session:
            await session["scraper"].cerrar()

    async def _cerrar_sesion(self, token: str):
        await self.cerrar(token)

    async def cerrar_todas(self):
        for token in list(self._sessions.keys()):
            await self.cerrar(token)


session_store = SessionStore()


def cancelar_renovacion():
    if auto_renovacion["job_id"]:
        try:
            scheduler.remove_job(auto_renovacion["job_id"])
        except Exception:
            pass
    auto_renovacion["activo"] = False
    auto_renovacion["job_id"] = None


def programar_renovacion(fecha_expiracion_str: str):
    try:
        fecha_exp = datetime.strptime(fecha_expiracion_str, "%d/%m/%y %H:%M")
        fecha_exp = ARG_TZ.localize(fecha_exp)
        limite_20hs = fecha_exp.replace(hour=20, minute=0, second=0, microsecond=0)

        if fecha_exp >= limite_20hs:
            fecha_ejecucion = limite_20hs
            solo_finalizar = True
        else:
            fecha_ejecucion = fecha_exp
            solo_finalizar = False

        cancelar_renovacion()

        job_id = f"renovacion_{uuid.uuid4().hex[:8]}"
        scheduler.add_job(
            ejecutar_renovacion,
            trigger="date",
            run_date=fecha_ejecucion,
            id=job_id,
            kwargs={"solo_finalizar": solo_finalizar},
        )

        auto_renovacion["activo"] = True
        auto_renovacion["job_id"] = job_id
        print(f"Renovacion programada para: {fecha_ejecucion} (solo_finalizar={solo_finalizar})")

    except Exception as e:
        print(f"Error programando renovacion: {e}")


async def ejecutar_renovacion(solo_finalizar: bool = False):
    print(f"Ejecutando renovacion automatica (solo_finalizar={solo_finalizar})")

    ahora = datetime.now(ARG_TZ)
    es_dia_habil = ahora.weekday() < 5
    es_horario = 9 <= ahora.hour < 20

    if not es_dia_habil:
        print("No es dia habil, cancelando renovacion automatica.")
        cancelar_renovacion()
        return

    usuario = auto_renovacion["usuario"]
    password = auto_renovacion["password"]

    if not usuario or not password:
        print("No hay credenciales guardadas.")
        cancelar_renovacion()
        return

    scraper = MParkingScraper(headless=True)
    try:
        await scraper.iniciar()
        resultado = await scraper.login(usuario, password)

        if not resultado["ok"]:
            print(f"Login fallido: {resultado['mensaje']}")
            return

        datos = await scraper._extraer_datos_usuario()
        estacionamientos = datos.get("estacionamientos_activos", [])

        # Si no hay estacionamiento activo, el usuario lo cancelo desde la web
        if not estacionamientos:
            print("No hay estacionamiento activo. Fue cancelado desde la web. Cancelando renovacion.")
            cancelar_renovacion()
            return

        # Verificar que la patente del estacionamiento activo coincide con la guardada
        patente_activa = estacionamientos[0].get("patente", "").strip().upper()
        patente_esperada = (auto_renovacion["patente"] or "").strip().upper()
        if patente_activa != patente_esperada:
            print(f"Patente activa ({patente_activa}) no coincide con la esperada ({patente_esperada}). Cancelando renovacion.")
            cancelar_renovacion()
            return

        # Finalizar el estacionamiento vencido
        await scraper.finalizar_estacionamiento(0)
        print("Estacionamiento finalizado automaticamente.")

        if solo_finalizar or not es_horario:
            print("Finalizacion a las 20:00 completada.")
            cancelar_renovacion()
            return

        # Navegar al formulario y cargar calles/alturas antes de estacionar
        # (igual que el flujo manual, el formulario tiene que estar cargado)
        await scraper.obtener_calles()
        await scraper.obtener_alturas(auto_renovacion["valor_calle"])

        resultado_est = await scraper.estacionar_vehiculo(
            auto_renovacion["valor_calle"],
            auto_renovacion["valor_altura"],
            auto_renovacion["patente"],
        )

        if resultado_est["ok"]:
            print(f"Estacionamiento renovado: {resultado_est}")
            ticket = resultado_est.get("ticket", {})
            vencimiento = ticket.get("vencimiento", "")
            if vencimiento:
                programar_renovacion(vencimiento)
        else:
            print(f"Error al renovar: {resultado_est['mensaje']}")
            cancelar_renovacion()

    except Exception as e:
        print(f"Error en renovacion automatica: {e}")
        cancelar_renovacion()
    finally:
        await scraper.cerrar()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    print("MParking API iniciada")
    yield
    scheduler.shutdown()
    await session_store.cerrar_todas()
    print("MParking API detenida")


app = FastAPI(
    title="MParking API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    usuario: str
    password: str

class LoginResponse(BaseModel):
    ok: bool
    mensaje: str
    session_token: Optional[str] = None
    datos: Optional[dict] = None

class CuentaResponse(BaseModel):
    ok: bool
    celular: Optional[str] = None
    saldo_disponible: Optional[str] = None
    saldo: Optional[str] = None
    estacionamientos_activos: Optional[list] = None
    patentes: Optional[list] = None

class EstacionarRequest(BaseModel):
    token: str
    valor_calle: str
    valor_altura: str
    patente: str


@app.get("/")
async def root():
    return {"status": "ok", "mensaje": "MParking API funcionando"}


@app.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    scraper = MParkingScraper(headless=True)
    await scraper.iniciar()
    resultado = await scraper.login(body.usuario, body.password)
    if not resultado["ok"]:
        await scraper.cerrar()
        return LoginResponse(ok=False, mensaje=resultado["mensaje"])
    token = session_store.crear(scraper)
    auto_renovacion["usuario"] = body.usuario
    auto_renovacion["password"] = body.password
    return LoginResponse(
        ok=True,
        mensaje=resultado["mensaje"],
        session_token=token,
        datos=resultado.get("datos"),
    )


@app.post("/logout")
async def logout(token: str):
    scraper = session_store.obtener(token)
    if not scraper:
        raise HTTPException(status_code=404, detail="Sesion no encontrada o expirada")
    await session_store.cerrar(token)
    return {"ok": True, "mensaje": "Sesion cerrada correctamente"}


@app.get("/cuenta", response_model=CuentaResponse)
async def cuenta(token: str):
    scraper = session_store.obtener(token)
    if not scraper:
        raise HTTPException(status_code=401, detail="Sesion invalida o expirada.")
    datos = await scraper._extraer_datos_usuario()
    return CuentaResponse(
        ok=True,
        celular=datos.get("celular"),
        saldo_disponible=datos.get("saldo_disponible"),
        saldo=datos.get("saldo"),
        estacionamientos_activos=datos.get("estacionamientos_activos", []),
        patentes=datos.get("patentes", []),
    )


@app.get("/estacionar/calles")
async def estacionar_calles(token: str):
    scraper = session_store.obtener(token)
    if not scraper:
        raise HTTPException(status_code=401, detail="Sesion invalida o expirada.")
    calles = await scraper.obtener_calles()
    return {"ok": True, "calles": calles}


@app.get("/estacionar/alturas")
async def estacionar_alturas(token: str, valor_calle: str):
    scraper = session_store.obtener(token)
    if not scraper:
        raise HTTPException(status_code=401, detail="Sesion invalida o expirada.")
    alturas = await scraper.obtener_alturas(valor_calle)
    return {"ok": True, "alturas": alturas}


@app.post("/estacionar")
async def estacionar(body: EstacionarRequest):
    scraper = session_store.obtener(body.token)
    if not scraper:
        raise HTTPException(status_code=401, detail="Sesion invalida o expirada.")
    resultado = await scraper.estacionar_vehiculo(
        body.valor_calle,
        body.valor_altura,
        body.patente,
    )
    print(f"Resultado estacionar: {resultado}")
    if not resultado["ok"]:
        raise HTTPException(status_code=400, detail=resultado["mensaje"])

    auto_renovacion["valor_calle"] = body.valor_calle
    auto_renovacion["valor_altura"] = body.valor_altura
    auto_renovacion["patente"] = body.patente
    ticket = resultado.get("ticket", {})
    vencimiento = ticket.get("vencimiento", "")
    if vencimiento:
        programar_renovacion(vencimiento)

    return resultado


@app.post("/estacionar/finalizar")
async def finalizar_estacionamiento(token: str, indice: int = 0):
    scraper = session_store.obtener(token)
    if not scraper:
        raise HTTPException(status_code=401, detail="Sesion invalida o expirada.")
    resultado = await scraper.finalizar_estacionamiento(indice)
    if not resultado["ok"]:
        raise HTTPException(status_code=400, detail=resultado["mensaje"])
    cancelar_renovacion()
    print("Renovacion automatica cancelada por finalizacion manual.")
    return resultado


@app.get("/renovacion/estado")
async def estado_renovacion():
    ahora = datetime.now(ARG_TZ)
    job = None
    if auto_renovacion["job_id"]:
        try:
            job = scheduler.get_job(auto_renovacion["job_id"])
        except Exception:
            pass
    return {
        "activo": auto_renovacion["activo"],
        "patente": auto_renovacion["patente"],
        "proxima_ejecucion": str(job.next_run_time) if job else None,
        "hora_actual_argentina": ahora.strftime("%d/%m/%Y %H:%M:%S"),
    }


@app.get("/debug/html")
async def debug_html(token: str):
    scraper = session_store.obtener(token)
    if not scraper:
        raise HTTPException(status_code=401, detail="Sesion invalida")
    html = await scraper.obtener_html_actual()
    return {"html": html}
