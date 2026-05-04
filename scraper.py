"""
scraper.py
----------
Módulo de web scraping para moviltr.com.ar/MParkingSelfcare/
Usa Playwright (headless browser) para manejar sesiones, cookies y JS.

IMPORTANTE: Antes de usar, instalá las dependencias:
    pip install playwright
    playwright install chromium
"""

import asyncio
from typing import Optional
from playwright.async_api import async_playwright, Page, BrowserContext

# URL base del portal
BASE_URL = "https://moviltr.com.ar/MParkingSelfcare/"

# ─────────────────────────────────────────────
# AJUSTÁ ESTOS SELECTORES según la página real
# Para encontrarlos: abrí la página en Chrome →
# clic derecho en el campo → "Inspeccionar"
# ─────────────────────────────────────────────
SELECTORS = {
    # Menú lateral: ítem "Mi cuenta"
    # Probamos varias variantes comunes; el primero que matchee gana (ver _click_mi_cuenta)
    "mi_cuenta": [
        "a:has-text('Mi Cuenta')",   # texto exacto del menú
        "a:has-text('Mi cuenta')",
        "li:has-text('Mi Cuenta') a",
    ],
    # Formulario de login
    "usuario": "input[name='usernameBorder:usernameBorder_body:username']",
    "password": "input[name='passwordBorder:passwordBorder_body:password']",
    "btn_login": "button.btn-success[type='submit']",  # botón "Aceptar"
    "error_msg": "li.feedbackPanelERROR span",  # mensaje de credenciales inválidas
    # Estacionar Vehículo
    "estacionar_vehiculo_menu": "a:has-text('Estacionar Vehículo')",
    "tipo_pago":    "select[name='paymentType']",
    "calle":        "select[name='parkStreetPaymentPanel:streetNameBorder:streetNameBorder_body:streetName']",
    "altura":       "select[name='parkStreetPaymentPanel:streetBorder:streetBorder_body:street']",
    "patente_est":  "input[name='parkStreetPaymentPanel:carLicenseBorder:carLicenseBorder_body:carLicense']",
    "btn_estacionar": "button.btn-success[type='submit']",

    # Dashboard post-login
    "usuario_logueado": "div.user > span",                  # número de celular del usuario
    "saldo_disponible": "p:has-text('Saldo disponible') span",  # saldo disponible
    "saldo":            "p:has-text('Saldo:') span",            # saldo total
    "estacionamientos": "h1:has-text('Estacionamientos') + span",  # sección estac. activos
    "sin_estacionamiento": "div.alert.alert-info",          # mensaje "sin estacionamiento"
    "patentes":         "#id6",                             # sección de patentes
}


class MParkingScraper:
    """
    Scraper para el portal MParkingSelfcare.
    Mantiene una sesión de navegador por instancia.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def iniciar(self):
        """Inicia el navegador y crea un contexto de sesión."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Linux; Android 12; Pixel 6) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/112.0.0.0 Mobile Safari/537.36"
            ),
            locale="es-AR",
            timezone_id="America/Argentina/Buenos_Aires",
        )
        self._page = await self._context.new_page()

    async def cerrar(self):
        """Cierra el navegador y libera recursos."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _click_mi_cuenta(self) -> bool:
        """
        Busca y hace clic en el ítem 'Mi cuenta' del menú lateral.
        Prueba múltiples selectores hasta encontrar el elemento.

        Retorna True si lo encontró y clickeó, False si no.
        """
        for selector in SELECTORS["mi_cuenta"]:
            try:
                elem = await self._page.wait_for_selector(
                    selector, timeout=5_000, state="visible"
                )
                if elem:
                    await elem.click()
                    await self._page.wait_for_load_state("networkidle", timeout=15_000)
                    print(f"✅ 'Mi cuenta' encontrado con selector: {selector}")
                    return True
            except Exception:
                continue  # Probar el siguiente selector

        # Si ningún selector funcionó, intentar por texto exacto como fallback
        try:
            await self._page.get_by_role("link", name="Mi cuenta").click()
            await self._page.wait_for_load_state("networkidle", timeout=15_000)
            print("✅ 'Mi cuenta' encontrado por role+text")
            return True
        except Exception:
            pass

        return False

    async def login(self, usuario: str, password: str) -> dict:
        """
        Realiza el login en el portal.

        Parámetros:
            usuario: número de celular de 10 dígitos (ej: 3415778972, sin 0 ni 15)
            password: contraseña del portal

        Retorna un dict con:
            {
                "ok": True/False,
                "mensaje": "descripción del resultado",
                "datos": { ... }   # datos del usuario si el login fue exitoso
            }
        """
        if not self._page:
            raise RuntimeError("Llamá a iniciar() antes de usar el scraper.")

        try:
            # 1. Navegar al portal
            await self._page.goto(BASE_URL, wait_until="networkidle", timeout=30_000)

            # 2. Hacer clic en "Mi cuenta" del menú lateral para llegar al login
            encontrado = await self._click_mi_cuenta()
            if not encontrado:
                return {
                    "ok": False,
                    "mensaje": (
                        "No se encontró el botón 'Mi cuenta' en el menú. "
                        "Revisá el selector en SELECTORS['mi_cuenta'] dentro de scraper.py."
                    ),
                    "datos": None,
                }

            # 3. Completar el formulario de login
            await self._page.wait_for_selector(SELECTORS["usuario"], timeout=10_000)
            await self._page.fill(SELECTORS["usuario"], usuario)
            await self._page.fill(SELECTORS["password"], password)

            # 4. Hacer clic en el botón y esperar navegación
            await asyncio.gather(
                self._page.wait_for_load_state("networkidle"),
                self._page.click(SELECTORS["btn_login"]),
            )

            # 5. Verificar si hubo error de login
            error_elem = await self._page.query_selector(SELECTORS["error_msg"])
            if error_elem:
                mensaje_error = await error_elem.inner_text()
                return {
                    "ok": False,
                    "mensaje": mensaje_error.strip(),
                    "datos": None,
                }

            # 6. Login exitoso → extraer datos disponibles
            datos = await self._extraer_datos_usuario()
            return {
                "ok": True,
                "mensaje": "Login exitoso",
                "datos": datos,
            }

        except Exception as e:
            return {
                "ok": False,
                "mensaje": f"Error inesperado: {str(e)}",
                "datos": None,
            }

    async def _navegar_dashboard(self):
        """Navega al dashboard (Mi Cuenta) si no estamos ya ahí."""
        if "activePark" not in self._page.url and "yOX21" not in self._page.url:
            await self._page.click(SELECTORS["mi_cuenta"][0])
            await self._page.wait_for_load_state("networkidle")

    async def _extraer_datos_usuario(self) -> dict:
        """
        Extrae información del dashboard post-login:
        - celular del usuario
        - saldo disponible y saldo total
        - estacionamientos activos (o mensaje de que no hay)
        - patentes asociadas (o mensaje de que no hay)
        """
        # Asegurarse de estar en el dashboard
        await self._navegar_dashboard()

        datos = {}

        # Celular del usuario logueado
        elem = await self._page.query_selector(SELECTORS["usuario_logueado"])
        if elem:
            datos["celular"] = (await elem.inner_text()).strip()

        # Saldo disponible
        elem = await self._page.query_selector(SELECTORS["saldo_disponible"])
        if elem:
            datos["saldo_disponible"] = (await elem.inner_text()).strip()

        # Saldo total
        elem = await self._page.query_selector(SELECTORS["saldo"])
        if elem:
            datos["saldo"] = (await elem.inner_text()).strip()

        # Estacionamientos activos: verificar si existe la tabla
        tabla = await self._page.query_selector("table.table")
        if tabla:
            datos["estacionamientos_activos"] = await self._extraer_estacionamientos_activos()
        else:
            datos["estacionamientos_activos"] = []

        # Patentes asociadas
        patentes_section = await self._page.query_selector(SELECTORS["patentes"])
        if patentes_section:
            sin_patente = await patentes_section.query_selector(".alert-info")
            if sin_patente:
                datos["patentes"] = []
            else:
                items = await patentes_section.query_selector_all(".patente, td.patente, span.patente")
                datos["patentes"] = [
                    (await item.inner_text()).strip() for item in items
                ]

        return datos

    async def obtener_calles(self) -> list[dict]:
        """
        Navega a "Estacionar Vehículo", selecciona "Pago diferido" y
        retorna la lista de calles disponibles como:
            [{ "value": "123", "label": "AV PELLEGRINI" }, ...]
        """
        if not self._page:
            raise RuntimeError("Llamá a iniciar() antes de usar el scraper.")

        # Navegar a la sección
        await self._page.click(SELECTORS["estacionar_vehiculo_menu"])
        await self._page.wait_for_load_state("networkidle")

        # Seleccionar "Pago diferido"
        await self._page.select_option(SELECTORS["tipo_pago"], label="Pago diferido")
        await self._page.wait_for_load_state("networkidle")

        # Esperar que aparezca el select de calles
        await self._page.wait_for_selector(SELECTORS["calle"], timeout=10_000)

        # Extraer opciones (ignorar el placeholder vacío)
        opciones = await self._page.eval_on_selector(
            SELECTORS["calle"],
            """select => Array.from(select.options)
                .filter(o => o.value !== '')
                .map(o => ({ value: o.value, label: o.text.trim() }))
            """
        )
        return opciones

    async def obtener_alturas(self, valor_calle: str) -> list[dict]:
        """
        Selecciona una calle y retorna las alturas disponibles.
        Debe llamarse después de obtener_calles().

        Parámetros:
            valor_calle: el campo 'value' de la calle elegida (ej: "123")

        Retorna:
            [{ "value": "456", "label": "100 - AV PELLEGRINI" }, ...]
        """
        if not self._page:
            raise RuntimeError("Llamá a iniciar() antes de usar el scraper.")

        # Seleccionar la calle disparando el evento change (igual que en Selenium con JS)
        await self._page.eval_on_selector(
            SELECTORS["calle"],
            """(select, value) => {
                const option = Array.from(select.options).find(o => o.value === value);
                if (option) {
                    option.selected = true;
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }""",
            valor_calle,
        )

        # Esperar que el select de alturas se cargue con opciones reales
        # Embebemos el selector directamente en el JS para evitar pasar args posicionales
        selector_altura = SELECTORS["altura"].replace('"', '\"')
        await self._page.wait_for_function(
            f"""() => {{
                const el = document.querySelector("{selector_altura}");
                return el && el.options.length > 1;
            }}""",
            timeout=30_000,
        )

        opciones = await self._page.eval_on_selector(
            SELECTORS["altura"],
            """select => Array.from(select.options)
                .filter(o => o.value !== '')
                .map(o => ({ value: o.value, label: o.text.trim() }))
            """
        )
        return opciones

    async def estacionar_vehiculo(
        self,
        valor_calle: str,
        valor_altura: str,
        patente: str,
    ) -> dict:
        """
        Completa y envía el formulario de estacionamiento (dos pasos):
          1. Completa calle, altura y patente → click "Aceptar" (button submit)
          2. Pantalla de confirmación con datos del ticket → click "Aceptar" (link)

        Retorna el ticket completo si fue exitoso:
            {
                "ok": True,
                "ticket": {
                    "patente": "ABC123",
                    "vencimiento": "16/03/26 20:00",
                    "tiempo_maximo": "0:24",
                    "importe_maximo": "383.39",
                    "zona": "Verde",
                    "calle": "Dorrego 0",
                    "cuadra": "3297",
                    "tarifa": "Normal",
                    "tipo_vehiculo": "Auto",
                }
            }
        """
        if not self._page:
            raise RuntimeError("Llamá a iniciar() antes de usar el scraper.")

        try:
            # ── PASO 1: completar el formulario ───────────────────────────────

            # Seleccionar altura
            await self._page.eval_on_selector(
                SELECTORS["altura"],
                """(select, value) => {
                    const option = Array.from(select.options).find(o => o.value === value);
                    if (option) {
                        option.selected = true;
                        select.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }""",
                valor_altura,
            )

            # Ingresar patente con JS (evita que el campo se limpie solo)
            await self._page.eval_on_selector(
                SELECTORS["patente_est"],
                """(input, patente) => {
                    input.value = patente;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                patente.upper(),
            )

            # Click en el primer "Aceptar" (button submit del formulario)
            await asyncio.gather(
                self._page.wait_for_load_state("networkidle"),
                self._page.click(SELECTORS["btn_estacionar"]),
            )

            # Verificar si hubo error en el paso 1
            error = await self._page.query_selector(SELECTORS["error_msg"])
            if error:
                return {"ok": False, "mensaje": (await error.inner_text()).strip()}

            # ── PASO 2: pantalla de confirmación ─────────────────────────────

            # Esperar que cargue la pantalla de confirmación
            await self._page.wait_for_selector("div.ticket", timeout=10_000)

            # Extraer los datos del ticket antes de confirmar
            ticket = await self._extraer_ticket()

            # Click en el segundo "Aceptar" (es un <a>, no un <button>)
            # Buscamos el link verde dentro de la sección de confirmación
            await asyncio.gather(
                self._page.wait_for_load_state("networkidle"),
                self._page.click("div.text-center > a.btn-success"),
            )

            # Verificar error post-confirmación
            error = await self._page.query_selector(SELECTORS["error_msg"])
            if error:
                return {"ok": False, "mensaje": (await error.inner_text()).strip()}

            return {
                "ok": True,
                "mensaje": "Vehiculo estacionado correctamente",
                "ticket": ticket,
            }

        except Exception as e:
            return {"ok": False, "mensaje": f"Error inesperado: {str(e)}"}

    async def _extraer_estacionamientos_activos(self) -> list[dict]:
        """
        Parsea la tabla de estacionamientos activos del dashboard.
        Cada fila incluye un índice para poder llamar a finalizar_estacionamiento().
        """
        filas = await self._page.query_selector_all("table.table tbody tr:not(:first-child)")
        estacionamientos = []
        for i, fila in enumerate(filas):
            spans = await fila.query_selector_all("td span")
            valores = [(await s.inner_text()).strip() for s in spans]
            if len(valores) < 13:
                continue
            # Verificar si tiene link "Fin"
            fin_link = await fila.query_selector("td a")
            estacionamientos.append({
                "indice":          i,
                "patente":         valores[0],
                "fecha_inicio":    valores[1],
                "fecha_expiracion":valores[2],
                "tiempo":          valores[3],
                "importe":         valores[4],
                "tiempo_maximo":   valores[5],
                "importe_maximo":  valores[6],
                "zona":            valores[7],
                "cuadra":          valores[8],
                "calle":           valores[9],
                "tarifa":          valores[10],
                "tipo_vehiculo":   valores[11],
                "estado":          valores[12],
                "puede_finalizar": fin_link is not None,
            })
        return estacionamientos

    async def finalizar_estacionamiento(self, indice: int) -> dict:
        """
        Hace click en el link "Fin" de un estacionamiento activo.

        Parámetros:
            indice: el campo 'indice' devuelto por _extraer_estacionamientos_activos

        Retorna:
            { "ok": True/False, "mensaje": "..." }
        """
        if not self._page:
            raise RuntimeError("Llamá a iniciar() antes de usar el scraper.")

        try:
            # Navegar al dashboard si no estamos ahí
            await self._page.wait_for_selector("table.table", timeout=5_000)

            # Buscar todas las filas con link "Fin"
            filas = await self._page.query_selector_all("table.table tbody tr:not(:first-child)")
            if indice >= len(filas):
                return {"ok": False, "mensaje": f"Indice {indice} fuera de rango"}

            fin_link = await filas[indice].query_selector("td a")
            if not fin_link:
                return {"ok": False, "mensaje": "Este estacionamiento no tiene opcion de finalizar"}

            # Click en "Fin" y esperar navegación
            await asyncio.gather(
                self._page.wait_for_load_state("networkidle"),
                fin_link.click(),
            )

            # Verificar error
            error = await self._page.query_selector(SELECTORS["error_msg"])
            if error:
                return {"ok": False, "mensaje": (await error.inner_text()).strip()}

            return {"ok": True, "mensaje": "Estacionamiento finalizado correctamente"}

        except Exception as e:
            return {"ok": False, "mensaje": f"Error inesperado: {str(e)}"}

    async def _extraer_ticket(self) -> dict:
        """
        Extrae los datos del ticket de la pantalla de confirmación.
        """
        async def _texto(selector: str) -> str:
            el = await self._page.query_selector(selector)
            return (await el.inner_text()).strip() if el else ""

        # Los spans dentro de div.ticket contienen los valores en orden
        spans = await self._page.query_selector_all("div.ticket span")
        valores = [(await s.inner_text()).strip() for s in spans]

        # Mapear por posición según el HTML conocido:
        # [0]=patente, [1]=vencimiento, [2]=tiempo_max, [3]=importe_max,
        # [4]=tiempo, [5]=importe, [6]=zona, [7]=calle, [8]=cuadra,
        # [9]=tarifa, [10]=tipo_vehiculo
        keys = [
            "patente", "vencimiento", "tiempo_maximo", "importe_maximo",
            "tiempo", "importe", "zona", "calle", "cuadra",
            "tarifa", "tipo_vehiculo",
        ]
        ticket = {}
        for i, key in enumerate(keys):
            ticket[key] = valores[i] if i < len(valores) else ""

        return ticket

    async def obtener_html_actual(self) -> str:
        """Retorna el HTML completo de la página actual (útil para debug)."""
        if not self._page:
            raise RuntimeError("Navegador no iniciado.")
        return await self._page.content()


# ─────────────────────────────────────────────
# Test rápido (ejecutar directo con Python)
# ─────────────────────────────────────────────
async def _test():
    scraper = MParkingScraper(headless=False)  # headless=False para ver el browser
    await scraper.iniciar()

    resultado = await scraper.login(
        usuario="TU_USUARIO",
        password="TU_PASSWORD",
    )
    print(resultado)

    await scraper.cerrar()


if __name__ == "__main__":
    asyncio.run(_test())
