"""Pruebas del flujo completo con carpetas temporales y un servidor SMTP falso."""
import base64
import email
import hashlib
import json
import smtplib
import socketserver
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from confauna import (autopiloto, bajas, buzon, config, diagnostico, envio, lotes, oauth,  # noqa: E402
                      plantillas, prospectos, verificacion)

RAIZ = Path(__file__).resolve().parent.parent


class SmtpFalso:
    def __init__(self, rechazar=(), fallar_en=None):
        self.enviados, self.rechazar, self.fallar_en = [], set(rechazar), fallar_en
        self.cerrado = False

    def login(self, u, c):
        self.login_ok = (u, c)

    def send_message(self, msg):
        if msg["To"] in self.rechazar:
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no existe")})
        if self.fallar_en is not None and len(self.enviados) == self.fallar_en:
            raise smtplib.SMTPDataError(421, b"limite")
        self.enviados.append(msg)

    def quit(self):
        self.cerrado = True


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / "dossier").mkdir()
        (t / "dossier" / "d.pdf").write_bytes(b"%PDF-1.4 falso")
        parches = {
            "DATA_DIR": t / "data", "LOTES_DIR": t / "lotes",
            "TEMPLATES_DIR": RAIZ / "templates", "DOSSIER_PATH": t / "dossier" / "d.pdf",
            "PROSPECTOS_CSV": t / "data" / "prospectos.csv", "BAJAS_TXT": t / "data" / "bajas.txt",
            "REGISTRO_CSV": t / "data" / "registro_envios.csv",
        }
        self.parches = [patch.object(config, k, v) for k, v in parches.items()]
        env = {"GMAIL_USER": "yo@gmail.com", "GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop",
               "LIMITE_DIARIO": "3", "PAUSA_MIN_SEG": "0", "PAUSA_MAX_SEG": "0"}
        self.parches.append(patch.dict("os.environ", env))
        for p in self.parches:
            p.start()
        self.t = t

    def tearDown(self):
        for p in self.parches:
            p.stop()
        self.tmp.cleanup()

    def cargar_prospectos(self, n=5, segmento="administradores"):
        csv_nuevo = self.t / "nuevos.csv"
        lineas = ["segmento,nombre,persona,email,municipio,fuente"]
        for i in range(1, n + 1):
            persona = "Ana García" if i == 1 else ""
            lineas.append(f"{segmento},Despacho {i},{persona},d{i}@example.com,Málaga,el directorio X")
        csv_nuevo.write_text("\n".join(lineas), encoding="utf-8")
        return prospectos.importar(csv_nuevo)

    def aprobar_todo(self, ruta):
        filas = lotes.leer_lote(ruta)
        for f in filas:
            f["aprobado"] = "SI"
        lotes._escribir_lote(ruta, filas)


class TestImportar(Base):
    def test_filtra_invalidos_duplicados_y_bajas(self):
        bajas.anadir("d3@example.com")
        (self.t / "n.csv").write_text(
            "segmento,nombre,email\n"
            "administradores,A,a@example.com\n"
            "administradores,A otra vez,A@example.com\n"
            "administradores,B,no-es-email\n"
            "administradores,C,d3@example.com\n"
            "bares,D,d@example.com\n", encoding="utf-8")
        r = prospectos.importar(self.t / "n.csv")
        self.assertEqual(r, {"añadidos": 1, "duplicados": 1, "invalidos": 1, "bajas": 1, "segmento_desconocido": 1})


class TestPlantillas(Base):
    def test_las_cuatro_plantillas_renderizan_sin_huecos(self):
        aj = config.ajustes()
        fila = {"nombre": "Hotel Sol", "persona": "", "municipio": "Marbella", "fuente": "el Registro de Turismo"}
        for nombre in ("administradores", "hoteles", "ayuntamientos", "fotovoltaica", "agroindustria", "recordatorio"):
            asunto, cuerpo = plantillas.renderizar(nombre, plantillas.contexto(aj, fila))
            self.assertTrue(asunto, nombre)
            self.assertNotIn("{", asunto + cuerpo, nombre)
            self.assertIn("BAJA", cuerpo, nombre)
            self.assertIn("600 000 000", cuerpo, nombre)
        self.assertIn("halconero", plantillas.renderizar("hoteles", plantillas.contexto(aj, fila))[1])

    def test_saludo_con_y_sin_persona(self):
        aj = config.ajustes()
        self.assertTrue(plantillas.contexto(aj, {"persona": "Ana"})["saludo"].startswith("Buenos días, Ana"))
        self.assertEqual(plantillas.contexto(aj, {"persona": ""})["saludo"], "Buenos días:")


class TestFlujo(Base):
    def test_preparar_aprobar_enviar_y_seguimiento(self):
        self.cargar_prospectos(5)
        ruta, n = lotes.preparar("administradores", 4, ahora=datetime(2026, 10, 27, 10, 0))
        self.assertEqual(n, 4)
        estados = [f["estado"] for f in prospectos.cargar()]
        self.assertEqual(estados.count("borrador"), 4)
        self.assertEqual(estados.count("nuevo"), 1)

        # Se aprueban solo 3 de 4: la cuarta se descarta.
        filas = lotes.leer_lote(ruta)
        for f in filas[:3]:
            f["aprobado"] = "SI"
        filas[3]["aprobado"] = "NO"
        lotes._escribir_lote(ruta, filas)

        # Simulación: no envía nada ni toca estados.
        r = envio.enviar_lote(ruta, real=False, salida=lambda *_: None)
        self.assertTrue(r["simulacion"])
        self.assertEqual([f["estado"] for f in prospectos.cargar()].count("enviado"), 0)

        smtp = SmtpFalso()
        hoy = date(2026, 10, 27)
        r = envio.enviar_lote(ruta, real=True, hoy=hoy, smtp_factory=lambda: smtp,
                              dormir=lambda s: None, salida=lambda *_: None)
        self.assertEqual(r["enviados"], 3)
        self.assertEqual(len(smtp.enviados), 3)
        self.assertTrue(smtp.cerrado)
        msg = smtp.enviados[0]
        self.assertEqual(msg["List-Unsubscribe"], "<mailto:yo@gmail.com?subject=BAJA>")
        self.assertEqual([p.get_filename() for p in msg.iter_attachments()], ["d.pdf"])
        estados = [f["estado"] for f in prospectos.cargar()]
        self.assertEqual(estados.count("enviado"), 3)
        self.assertEqual(estados.count("nuevo"), 2)  # la descartada vuelve a 'nuevo'

        # Repetir el mismo lote no reenvía nada (idempotente).
        smtp2 = SmtpFalso()
        r = envio.enviar_lote(ruta, real=True, hoy=hoy, smtp_factory=lambda: smtp2,
                              dormir=lambda s: None, salida=lambda *_: None)
        self.assertEqual((r["enviados"], r["ya_enviadas"]), (0, 3))

        # Pasados 7 días, seguimiento: sin adjunto y solo a los que no han respondido.
        ruta2, n2 = lotes.preparar(None, 10, tipo="recordatorio", hoy=hoy + timedelta(days=7),
                                   ahora=datetime(2026, 11, 3, 10, 0))
        self.assertEqual(n2, 3)
        self.aprobar_todo(ruta2)
        smtp3 = SmtpFalso()
        envio.enviar_lote(ruta2, real=True, hoy=hoy + timedelta(days=7), smtp_factory=lambda: smtp3,
                          dormir=lambda s: None, salida=lambda *_: None)
        self.assertEqual(len(smtp3.enviados), 3)
        self.assertEqual(list(smtp3.enviados[0].iter_attachments()), [])
        self.assertEqual([f["estado"] for f in prospectos.cargar()].count("recordatorio_enviado"), 3)
        # Y no hay un tercer envío.
        self.assertEqual(lotes.preparar(None, 10, tipo="recordatorio", hoy=hoy + timedelta(days=30))[1], 0)

    def test_limite_diario_aplaza_el_resto(self):
        self.cargar_prospectos(5)
        ruta, _ = lotes.preparar("administradores", 5)
        self.aprobar_todo(ruta)
        smtp = SmtpFalso()
        hoy = date(2026, 10, 27)
        r = envio.enviar_lote(ruta, real=True, hoy=hoy, smtp_factory=lambda: smtp,
                              dormir=lambda s: None, salida=lambda *_: None)
        self.assertEqual((r["enviados"], r["aplazados"]), (3, 2))  # LIMITE_DIARIO=3
        # Al día siguiente salen los 2 que faltaban.
        smtp2 = SmtpFalso()
        r = envio.enviar_lote(ruta, real=True, hoy=hoy + timedelta(days=1), smtp_factory=lambda: smtp2,
                              dormir=lambda s: None, salida=lambda *_: None)
        self.assertEqual((r["enviados"], r["aplazados"]), (2, 0))

    def test_rebote_marca_estado_y_error_grave_detiene(self):
        self.cargar_prospectos(4)
        ruta, _ = lotes.preparar("administradores", 4)
        self.aprobar_todo(ruta)
        smtp = SmtpFalso(rechazar={"d1@example.com"}, fallar_en=1)
        with self.assertRaises(envio.ErrorEnvio):
            envio.enviar_lote(ruta, real=True, hoy=date(2026, 10, 27), smtp_factory=lambda: smtp,
                              dormir=lambda s: None, salida=lambda *_: None)
        por_email = {f["email"]: f["estado"] for f in prospectos.cargar()}
        self.assertEqual(por_email["d1@example.com"], "rebotado")
        self.assertEqual(por_email["d2@example.com"], "enviado")
        self.assertEqual(por_email["d3@example.com"], "borrador")  # no se tocó: se enviará otro día

    def test_una_baja_posterior_bloquea_el_envio(self):
        self.cargar_prospectos(2)
        ruta, _ = lotes.preparar("administradores", 2)
        self.aprobar_todo(ruta)
        bajas.anadir("d2@example.com")
        smtp = SmtpFalso()
        r = envio.enviar_lote(ruta, real=True, hoy=date(2026, 10, 27), smtp_factory=lambda: smtp,
                              dormir=lambda s: None, salida=lambda *_: None)
        self.assertEqual((r["enviados"], r["bajas"]), (1, 1))
        self.assertEqual([m["To"] for m in smtp.enviados], ["d1@example.com"])

    def test_sin_credenciales_no_envia(self):
        self.cargar_prospectos(1)
        ruta, _ = lotes.preparar("administradores", 1)
        self.aprobar_todo(ruta)
        with patch.dict("os.environ", {"GMAIL_APP_PASSWORD": ""}):
            with self.assertRaises(envio.ErrorEnvio):
                envio.enviar_lote(ruta, real=True, smtp_factory=SmtpFalso, salida=lambda *_: None)

    def test_revisar_interactivo(self):
        self.cargar_prospectos(3)
        ruta, _ = lotes.preparar("administradores", 3)
        respuestas = iter(["s", "n", "q"])
        r = lotes.revisar(ruta, entrada=lambda _: next(respuestas), salida=lambda *_: None)
        self.assertEqual(r, {"aprobados": 1, "descartados": 1, "pendientes": 1})


class TestBuzon(unittest.TestCase):
    PROS = {"d1@example.com", "d2@example.com", "d3@example.com"}

    def msg(self, remitente, asunto, cuerpo):
        return email.message_from_string(f"From: {remitente}\nSubject: {asunto}\n"
                                         f"Content-Type: text/plain; charset=utf-8\n\n{cuerpo}")

    def test_respuesta_normal_no_se_confunde_con_baja_por_la_cita(self):
        cita = '> Si no desea recibir más mensajes, responda "BAJA" a este correo'
        m = self.msg("Ana <d1@example.com>", "Re: Control de palomas", f"Nos interesa, llámeme.\n\nEl lun escribió:\n{cita}")
        self.assertEqual(buzon.clasificar(m, self.PROS), ("d1@example.com", "respondido"))

    def test_baja_explicita(self):
        m = self.msg("d2@example.com", "Re: Control", "BAJA\n\n> texto citado")
        self.assertEqual(buzon.clasificar(m, self.PROS), ("d2@example.com", "baja"))
        m = self.msg("d2@example.com", "Re: Control", "Por favor, no me escriba más.")
        self.assertEqual(buzon.clasificar(m, self.PROS), ("d2@example.com", "baja"))

    def test_pie_legal_con_la_palabra_baja_no_es_una_baja(self):
        # Caso real (09/11/2026): un despacho contestó "lo tendremos en cuenta" y su pie legal
        # decía "mándenos un e-mail con la palabra 'baja'". Se marcó como baja por error.
        cuerpo = ("Muchas gracias por su email.\nLo tendremos en cuenta si alguna comunidad lo solicitase.\n\n"
                  "Saludos,\n\nAdministración\n\nEjemplo Comunidades, S.L.\nC/ Falsa 1\n29000 Málaga\n\n"
                  "Tel: 952 00 00 00\n\nEste documento se dirige exclusivamente a su destinatario. Puede contener "
                  "información confidencial. Si no desea recibir más información de nuestra empresa, mándenos un "
                  "e-mail con la palabra \"baja\" en el apartado asunto\n\n-----Mensaje original-----\nDe: yo")
        m = self.msg("d1@example.com", "RE: Seguimiento: control de aves", cuerpo)
        self.assertEqual(buzon.clasificar(m, self.PROS), ("d1@example.com", "respondido"))

    def test_baja_al_principio_aunque_haya_pie_legal(self):
        cuerpo = "Por favor, denme de baja.\n\n--\nAVISO LEGAL: este mensaje es confidencial."
        m = self.msg("d2@example.com", "Re: Control", cuerpo)
        self.assertEqual(buzon.clasificar(m, self.PROS), ("d2@example.com", "baja"))

    def test_rebote(self):
        m = self.msg("Mail Delivery Subsystem <mailer-daemon@googlemail.com>", "Delivery Status Notification (Failure)",
                     "No se ha podido entregar el mensaje a d3@example.com porque la dirección no existe.")
        self.assertEqual(buzon.clasificar(m, self.PROS), ("d3@example.com", "rebotado"))

    def test_correo_ajeno_se_ignora(self):
        self.assertIsNone(buzon.clasificar(self.msg("otro@example.org", "Hola", "Hola"), self.PROS))


class TestVerificacion(Base):
    def test_bloque_con_avisos_y_descartes(self):
        (self.t / "n.csv").write_text(
            "segmento,nombre,email,municipio,fuente\n"
            "administradores,Despacho A,a@despachoa.es,Málaga,la web del despacho\n"
            "administradores,Despacho B,b@gmail.com,Marbella,la web del despacho\n"
            "administradores,Despacho C,c@despachoc.es,Ronda,\n", encoding="utf-8")
        prospectos.importar(self.t / "n.csv", estado_inicial="por_verificar")
        self.assertEqual({f["estado"] for f in prospectos.cargar()}, {"por_verificar"})

        salidas = []
        respuestas = iter(["e", "99", "e", "2"])  # primero un ID inexistente, luego descarta el #2
        r = verificacion.verificar_bloque(10, entrada=lambda _: next(respuestas), salida=salidas.append)
        texto = "\n".join(salidas)
        self.assertIn("correo personal (gmail.com)", texto)
        self.assertIn("sin fuente", texto)
        self.assertIn("no están en el bloque", texto)
        self.assertEqual((r["aprobados"], r["descartados"], r["pendientes"]), (2, 1, 0))
        estados = {f["email"]: f["estado"] for f in prospectos.cargar()}
        self.assertEqual(estados, {"a@despachoa.es": "nuevo", "b@gmail.com": "descartado", "c@despachoc.es": "nuevo"})

    def test_bloque_respeta_el_tamano_y_salir_no_cambia_nada(self):
        self.cargar_prospectos(5)
        for f in (filas := prospectos.cargar()):
            f["estado"] = "por_verificar"
        prospectos.guardar(filas)
        r = verificacion.verificar_bloque(2, entrada=lambda _: "q", salida=lambda *_: None)
        self.assertEqual(r["pendientes"], 5)
        r = verificacion.verificar_bloque(2, entrada=lambda _: "t", salida=lambda *_: None)
        self.assertEqual((r["aprobados"], r["pendientes"]), (2, 3))
        self.assertEqual([f["estado"] for f in prospectos.cargar()].count("nuevo"), 2)


class TestCalentamiento(Base):
    def test_el_limite_sube_cada_dia_hasta_el_tope(self):
        aj = {**config.ajustes(), "limite_diario": 20, "calentamiento": True}
        hoy = date(2026, 10, 27)
        self.assertEqual(envio.limite_efectivo(aj, hoy), 5)
        envio._anotar({"fecha": hoy.isoformat(), "hora": "10:00:00", "lote": "x", "id": "1", "tipo": "primero",
                       "email": "a@example.com", "segmento": "administradores", "resultado": "enviado", "detalle": ""})
        self.assertEqual(envio.limite_efectivo(aj, hoy), 5)
        self.assertEqual(envio.limite_efectivo(aj, hoy + timedelta(days=1)), 7)
        self.assertEqual(envio.limite_efectivo(aj, hoy + timedelta(days=4)), 13)
        self.assertEqual(envio.limite_efectivo(aj, hoy + timedelta(days=30)), 20)
        self.assertEqual(envio.limite_efectivo({**aj, "calentamiento": False}, hoy), 20)


class TestAutopiloto(Base):
    MARTES = datetime(2026, 10, 27, 10, 0)

    def setUp(self):
        super().setUp()
        p = patch.dict("os.environ", {"LIMITE_DIARIO": "20"})
        p.start()
        self.addCleanup(p.stop)

    def correr(self, smtp, ahora=None, sync=lambda salida=None: [], **kw):
        return autopiloto.ejecutar(real=True, ahora=ahora or self.MARTES, smtp_factory=lambda: smtp,
                                   dormir=lambda s: None, salida=lambda *_: None, sincronizar_fn=sync, **kw)

    def test_primer_dia_respeta_el_calentamiento_y_no_repite(self):
        self.cargar_prospectos(8)
        smtp = SmtpFalso()
        r = self.correr(smtp)
        self.assertEqual((r["estado"], r["enviados"]), ("ok", 5))  # primer día: 5
        self.assertEqual(self.correr(SmtpFalso())["enviados"], 0)  # mismo día: cupo agotado
        r = self.correr(SmtpFalso(), ahora=self.MARTES + timedelta(days=1))
        self.assertEqual(r["enviados"], 3)  # quedaban 3
        self.assertEqual([f["estado"] for f in prospectos.cargar()].count("enviado"), 8)

    def test_recordatorios_van_primero_y_sin_adjunto(self):
        self.cargar_prospectos(6)
        filas = prospectos.cargar()
        for f in filas[:3]:
            f["estado"], f["fecha_envio"] = "enviado", "2026-10-20"
        prospectos.guardar(filas)
        smtp = SmtpFalso()
        r = self.correr(smtp)
        self.assertEqual((r["recordatorios"], r["primeros"]), (3, 2))  # cupo de 5
        asuntos = [m["Subject"] for m in smtp.enviados]
        self.assertTrue(all(a.startswith("Seguimiento") for a in asuntos[:3]))
        self.assertEqual([len(list(m.iter_attachments())) for m in smtp.enviados], [0, 0, 0, 1, 1])

    def test_sin_buzon_no_hay_recordatorios_pero_si_primeros(self):
        self.cargar_prospectos(4)
        filas = prospectos.cargar()
        filas[0]["estado"], filas[0]["fecha_envio"] = "enviado", "2026-10-20"
        prospectos.guardar(filas)
        def roto(salida=None):
            raise RuntimeError("IMAP desactivado")
        smtp = SmtpFalso()
        r = self.correr(smtp, sync=roto)
        self.assertEqual((r["recordatorios"], r["primeros"]), (0, 3))

    def test_horario_y_fin_de_semana(self):
        self.cargar_prospectos(2)
        sabado = datetime(2026, 10, 31, 10, 0)
        noche = datetime(2026, 10, 27, 21, 0)
        for ahora in (sabado, noche):
            smtp = SmtpFalso()
            r = self.correr(smtp, ahora=ahora)
            self.assertEqual((r["estado"], len(smtp.enviados)), ("fuera_de_horario", 0))
        r = self.correr(SmtpFalso(), ahora=sabado, forzar_horario=True)
        self.assertEqual(r["enviados"], 2)

    def test_simulacion_no_toca_nada(self):
        self.cargar_prospectos(3)
        r = autopiloto.ejecutar(real=False, ahora=self.MARTES, salida=lambda *_: None)
        self.assertEqual(r["estado"], "simulacion")
        self.assertEqual({f["estado"] for f in prospectos.cargar()}, {"nuevo"})
        self.assertFalse(config.REGISTRO_CSV.exists())

    def test_freno_por_tasa_de_rebote(self):
        self.cargar_prospectos(25)
        filas = prospectos.cargar()
        for i, f in enumerate(filas[:20]):
            f["fecha_envio"] = "2026-10-20"
            f["estado"] = "rebotado" if i < 2 else "recordatorio_enviado"  # 2/20 = 10 %
        prospectos.guardar(filas)
        smtp = SmtpFalso()
        r = self.correr(smtp)
        self.assertEqual((r["estado"], len(smtp.enviados)), ("frenado", 0))
        self.assertTrue((config.DATA_DIR / "PAUSA.txt").exists())
        # Mientras exista PAUSA.txt no hace nada, aunque se vuelva a lanzar.
        self.assertEqual(self.correr(SmtpFalso())["estado"], "pausado")
        (config.DATA_DIR / "PAUSA.txt").unlink()

    def test_con_pocos_enviados_no_se_frena(self):
        self.cargar_prospectos(6)
        filas = prospectos.cargar()
        filas[0]["fecha_envio"], filas[0]["estado"] = "2026-10-20", "rebotado"  # 1/1 = 100 % pero muestra mínima
        prospectos.guardar(filas)
        self.assertEqual(self.correr(SmtpFalso())["estado"], "ok")

    def test_error_a_mitad_se_reanuda_sin_duplicar(self):
        self.cargar_prospectos(5)
        smtp1 = SmtpFalso(fallar_en=2)  # envía 2 y falla en el 3.º
        r = self.correr(smtp1)
        self.assertEqual((r["estado"], r["enviados"]), ("error", 2))
        smtp2 = SmtpFalso()
        r = self.correr(smtp2)
        self.assertEqual(r["enviados"], 3)
        todos = [m["To"] for m in smtp1.enviados + smtp2.enviados]
        self.assertEqual(sorted(todos), sorted(set(todos)))
        self.assertEqual(len(todos), 5)

    def test_no_reanuda_lotes_manuales_sin_revisar(self):
        self.cargar_prospectos(3)
        lotes.preparar("administradores", 3)  # lote manual, sin aprobar
        smtp = SmtpFalso()
        r = self.correr(smtp)
        self.assertEqual(len(smtp.enviados), 0)  # siguen en 'borrador': no son 'nuevo' ni 'auto_'
        self.assertEqual({f["estado"] for f in prospectos.cargar()}, {"borrador"})


class SmtpFalsoOAuth(SmtpFalso):
    """SmtpFalso que además registra el inicio de sesión por XOAUTH2."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.login_llamado = False
        self.auth_cadenas = []

    def login(self, u, c):
        self.login_llamado = True

    def ehlo_or_helo_if_needed(self):
        pass

    def auth(self, mecanismo, authobject, **kw):
        self.auth_cadenas.append((mecanismo, authobject(), authobject(b"{error}")))


class BaseOAuth(Base):
    def setUp(self):
        super().setUp()
        env = {"EMAIL_AUTH": "oauth", "GOOGLE_CLIENT_ID": "cliente-123.apps.googleusercontent.com",
               "GOOGLE_CLIENT_SECRET": "secreto", "EMAIL_USER": "info@confauna.test",
               "EMAIL_PASSWORD": "", "GMAIL_APP_PASSWORD": ""}
        p = patch.dict("os.environ", env)
        p.start()
        self.addCleanup(p.stop)
        oauth._cache.clear()
        self.addCleanup(oauth._cache.clear)

    def guardar_autorizacion(self, usuario="info@confauna.test", refresh="refresh-1"):
        oauth._guardar_token({"refresh_token": refresh, "usuario": usuario})


class TestOAuthToken(BaseOAuth):
    def test_cadena_xoauth2_tiene_el_formato_de_google(self):
        self.assertEqual(oauth.cadena_xoauth2("a@b.es", "tok"), "user=a@b.es\x01auth=Bearer tok\x01\x01")

    def test_renueva_el_token_y_lo_reutiliza_hasta_que_caduca(self):
        self.guardar_autorizacion()
        llamadas = []

        def post(url, datos):
            llamadas.append((url, datos))
            return {"access_token": f"acceso-{len(llamadas)}", "expires_in": 3600}

        aj = config.ajustes()
        reloj = [1000.0]
        t1 = oauth.token_de_acceso(aj, post=post, ahora=lambda: reloj[0])
        t2 = oauth.token_de_acceso(aj, post=post, ahora=lambda: reloj[0] + 100)
        self.assertEqual((t1, t2), ("acceso-1", "acceso-1"))
        self.assertEqual(len(llamadas), 1)
        url, datos = llamadas[0]
        self.assertEqual(url, oauth.TOKEN_URL)
        self.assertEqual(datos, {"client_id": "cliente-123.apps.googleusercontent.com", "client_secret": "secreto",
                                 "refresh_token": "refresh-1", "grant_type": "refresh_token"})
        self.assertEqual(oauth.token_de_acceso(aj, post=post, ahora=lambda: reloj[0] + 3600), "acceso-2")

    def test_errores_claros(self):
        aj = config.ajustes()
        with self.assertRaisesRegex(oauth.ErrorOAuth, "autorizar"):
            oauth.token_de_acceso(aj, post=lambda *_: {})  # sin autorización guardada
        self.guardar_autorizacion()
        with self.assertRaisesRegex(oauth.ErrorOAuth, "invalidado"):
            oauth.token_de_acceso(aj, post=lambda *_: {"error": "invalid_grant"})
        with self.assertRaisesRegex(oauth.ErrorOAuth, "invalid_client"):
            oauth.token_de_acceso(aj, post=lambda *_: {"error": "invalid_client"})
        self.guardar_autorizacion(usuario="otra@cuenta.es", refresh="refresh-2")
        with self.assertRaisesRegex(oauth.ErrorOAuth, "otra@cuenta.es"):
            oauth.token_de_acceso(aj, post=lambda *_: {"access_token": "x"})


class TestOAuthAutorizar(BaseOAuth):
    def navegador_falso(self, **extra):
        """Simula al usuario aceptando en el navegador: llama a la dirección de retorno con un código."""
        capturado = {}

        def abrir(url):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            capturado["q"] = {k: v[0] for k, v in q.items()}
            params = {"code": "codigo-de-prueba", "state": capturado["q"]["state"], **extra}
            destino = capturado["q"]["redirect_uri"] + "/?" + urllib.parse.urlencode(params)
            threading.Thread(target=lambda: urllib.request.urlopen(destino, timeout=5).read()).start()

        return abrir, capturado

    def test_flujo_completo_con_pkce(self):
        abrir, cap = self.navegador_falso()
        enviado = {}

        def post(url, datos):
            enviado.update(datos)
            return {"refresh_token": "refresh-nuevo", "access_token": "a", "scope": oauth.SCOPE}

        datos = oauth.autorizar(config.ajustes(), abrir=abrir, post=post, espera_max=10, salida=lambda *_: None)
        q = cap["q"]
        self.assertEqual((q["scope"], q["response_type"], q["code_challenge_method"], q["login_hint"]),
                         ("https://mail.google.com/", "code", "S256", "info@confauna.test"))
        self.assertTrue(q["redirect_uri"].startswith("http://127.0.0.1:"))
        self.assertEqual(q["access_type"], "offline")
        # El verificador enviado a Google debe corresponder al desafío de la URL (PKCE).
        esperado = base64.urlsafe_b64encode(hashlib.sha256(enviado["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(q["code_challenge"], esperado)
        self.assertTrue(43 <= len(enviado["code_verifier"]) <= 128)
        self.assertEqual((enviado["code"], enviado["grant_type"], enviado["redirect_uri"]),
                         ("codigo-de-prueba", "authorization_code", q["redirect_uri"]))
        self.assertEqual(datos["usuario"], "info@confauna.test")
        guardado = json.loads(config.ruta_token().read_text(encoding="utf-8"))
        self.assertEqual(guardado["refresh_token"], "refresh-nuevo")
        self.assertTrue(oauth.esta_autorizado())

    def test_si_el_usuario_rechaza_el_permiso(self):
        abrir, _ = self.navegador_falso()

        def abrir_error(url):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            destino = q["redirect_uri"][0] + "/?" + urllib.parse.urlencode({"error": "access_denied"})
            threading.Thread(target=lambda: urllib.request.urlopen(destino, timeout=5).read()).start()

        with self.assertRaisesRegex(oauth.ErrorOAuth, "access_denied"):
            oauth.autorizar(config.ajustes(), abrir=abrir_error, post=lambda *_: {}, espera_max=10, salida=lambda *_: None)
        self.assertFalse(oauth.esta_autorizado())

    def test_estado_incorrecto_y_respuesta_sin_refresh_token(self):
        def abrir_estado_falso(url):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            destino = q["redirect_uri"][0] + "/?" + urllib.parse.urlencode({"code": "x", "state": "otro"})
            threading.Thread(target=lambda: urllib.request.urlopen(destino, timeout=5).read()).start()

        with self.assertRaisesRegex(oauth.ErrorOAuth, "no corresponde"):
            oauth.autorizar(config.ajustes(), abrir=abrir_estado_falso, post=lambda *_: {}, espera_max=10, salida=lambda *_: None)
        abrir, _ = self.navegador_falso()
        with self.assertRaisesRegex(oauth.ErrorOAuth, "no entregó"):
            oauth.autorizar(config.ajustes(), abrir=abrir, post=lambda *_: {"access_token": "solo-acceso"},
                            espera_max=10, salida=lambda *_: None)
        self.assertFalse(oauth.esta_autorizado())

    def test_sin_client_id_avisa_antes_de_abrir_nada(self):
        with patch.dict("os.environ", {"GOOGLE_CLIENT_ID": ""}):
            with self.assertRaisesRegex(oauth.ErrorOAuth, "GOOGLE_CLIENT_ID"):
                oauth.autorizar(config.ajustes(), abrir=lambda u: self.fail("no debe abrir el navegador"),
                                salida=lambda *_: None)

    def test_tiempo_agotado(self):
        with self.assertRaisesRegex(oauth.ErrorOAuth, "a tiempo"):
            oauth.autorizar(config.ajustes(), abrir=lambda u: None, post=lambda *_: {}, espera_max=1, salida=lambda *_: None)


class ServidorSmtpMinimo:
    """Servidor SMTP de verdad (en local) que solo entiende EHLO, AUTH XOAUTH2 y QUIT."""

    def __init__(self, token_bueno="bueno"):
        self.token_bueno, self.recibido, self.respuestas_vacias = token_bueno, [], 0
        outer = self

        class Manejador(socketserver.StreamRequestHandler):
            def handle(self):
                w = lambda t: self.wfile.write((t + "\r\n").encode())  # noqa: E731
                w("220 prueba ESMTP")
                while True:
                    linea = self.rfile.readline().decode().strip()
                    if not linea:
                        return
                    if linea.upper().startswith("EHLO"):
                        w("250-prueba")
                        w("250 AUTH XOAUTH2")
                    elif linea.upper().startswith("AUTH XOAUTH2"):
                        cadena = base64.b64decode(linea.split(" ", 2)[2]).decode()
                        outer.recibido.append(cadena)
                        if f"auth=Bearer {outer.token_bueno}\x01" in cadena:
                            w("235 2.7.0 Accepted")
                        else:
                            w("334 " + base64.b64encode(b'{"status":"401"}').decode())
                            if self.rfile.readline().decode().strip() == "":
                                outer.respuestas_vacias += 1
                            w("535 5.7.8 Username and Password not accepted")
                    elif linea.upper() == "QUIT":
                        w("221 adios")
                        return

        self.servidor = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Manejador)
        self.servidor.daemon_threads = True
        self.puerto = self.servidor.server_address[1]
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()

    def cerrar(self):
        self.servidor.shutdown()
        self.servidor.server_close()


class TestXOAuth2ConSmtplibReal(unittest.TestCase):
    def test_login_correcto_y_rechazado_contra_un_servidor_smtp_real(self):
        srv = ServidorSmtpMinimo(token_bueno="bueno")
        self.addCleanup(srv.cerrar)
        s = smtplib.SMTP("127.0.0.1", srv.puerto, timeout=5)
        oauth.autenticar_smtp(s, "info@confauna.test", "", token="bueno")
        self.assertEqual(srv.recibido, ["user=info@confauna.test\x01auth=Bearer bueno\x01\x01"])
        s.close()

        s = smtplib.SMTP("127.0.0.1", srv.puerto, timeout=5)
        with self.assertRaises(smtplib.SMTPAuthenticationError) as cm:
            oauth.autenticar_smtp(s, "info@confauna.test", "", token="malo")
        self.assertEqual(cm.exception.smtp_code, 535)
        self.assertEqual(srv.respuestas_vacias, 1)  # contestó vacío al error, como pide el protocolo
        s.close()


class IMAPFalso:
    instancias = []

    def __init__(self, host, port=993):
        self.host, self.llamadas = host, []
        IMAPFalso.instancias.append(self)

    def login(self, u, c):
        self.llamadas.append(("login", u, c))

    def authenticate(self, mecanismo, callback):
        self.llamadas.append(("authenticate", mecanismo, callback(b""), callback(b"error")))

    def select(self, *a, **k):
        return "OK", [b"0"]

    def search(self, *a):
        return "OK", [b""]

    def logout(self):
        self.llamadas.append(("logout",))


class TestOAuthIntegracion(BaseOAuth):
    def test_envio_con_oauth_usa_auth_y_no_login(self):
        self.guardar_autorizacion()
        self.cargar_prospectos(2)
        ruta, _ = lotes.preparar("administradores", 2)
        self.aprobar_todo(ruta)
        smtp = SmtpFalsoOAuth()
        with patch.object(oauth, "token_de_acceso", return_value="tok-123"):
            r = envio.enviar_lote(ruta, real=True, hoy=date(2026, 10, 27), smtp_factory=lambda: smtp,
                                  dormir=lambda s: None, salida=lambda *_: None)
        self.assertEqual(r["enviados"], 2)
        self.assertFalse(smtp.login_llamado)
        mecanismo, primera, tras_error = smtp.auth_cadenas[0]
        self.assertEqual((mecanismo, primera, tras_error),
                         ("XOAUTH2", "user=info@confauna.test\x01auth=Bearer tok-123\x01\x01", ""))

    def test_envio_con_oauth_sin_autorizar_da_un_error_que_explica_que_hacer(self):
        self.cargar_prospectos(1)
        ruta, _ = lotes.preparar("administradores", 1)
        self.aprobar_todo(ruta)
        with self.assertRaisesRegex(envio.ErrorEnvio, "autorizar"):
            envio.enviar_lote(ruta, real=True, smtp_factory=SmtpFalsoOAuth, salida=lambda *_: None)

    def test_envio_con_oauth_sin_client_id(self):
        self.cargar_prospectos(1)
        ruta, _ = lotes.preparar("administradores", 1)
        self.aprobar_todo(ruta)
        with patch.dict("os.environ", {"GOOGLE_CLIENT_ID": ""}):
            with self.assertRaisesRegex(envio.ErrorEnvio, "GOOGLE_CLIENT_ID"):
                envio.enviar_lote(ruta, real=True, smtp_factory=SmtpFalsoOAuth, salida=lambda *_: None)

    def test_buzon_con_oauth_usa_authenticate(self):
        IMAPFalso.instancias.clear()
        with patch.object(buzon.imaplib, "IMAP4_SSL", IMAPFalso):
            buzon.revisar_buzon("info@confauna.test", "", {"a@x.es"}, servidor="imap.gmail.com", token="tok-9")
            buzon.revisar_buzon("info@confauna.test", "clave", {"a@x.es"}, servidor="imap.gmail.com")
        con_oauth, con_clave = IMAPFalso.instancias
        self.assertEqual(con_oauth.llamadas[0],
                         ("authenticate", "XOAUTH2", b"user=info@confauna.test\x01auth=Bearer tok-9\x01\x01", b""))
        self.assertEqual(con_clave.llamadas[0], ("login", "info@confauna.test", "clave"))

    def test_sincronizar_con_oauth_pasa_el_token(self):
        self.guardar_autorizacion()
        self.cargar_prospectos(1)
        filas = prospectos.cargar()
        filas[0]["estado"] = "enviado"
        prospectos.guardar(filas)
        recibido = {}

        def revisar(usuario, clave, emails, servidor=None, token=None):
            recibido.update(usuario=usuario, clave=clave, token=token, servidor=servidor)
            return [("d1@example.com", "respondido")]

        with patch.object(oauth, "token_de_acceso", return_value="tok-5"):
            r = buzon.sincronizar(salida=lambda *_: None, revisar=revisar)
        self.assertEqual(r, [("d1@example.com", "respondido")])
        self.assertEqual((recibido["token"], recibido["usuario"]), ("tok-5", "info@confauna.test"))
        self.assertEqual(prospectos.cargar()[0]["estado"], "respondido")

    def test_probar_acceso(self):
        self.guardar_autorizacion()
        salidas = []
        smtp, imap = SmtpFalsoOAuth(), IMAPFalso("imap.gmail.com")
        with patch.object(oauth, "token_de_acceso", return_value="tok-7"):
            ok = diagnostico.probar(salida=salidas.append, smtp_factory=lambda: smtp, imap_factory=lambda: imap)
        self.assertTrue(ok)
        self.assertIn("Todo listo para enviar.", salidas)
        self.assertEqual(imap.llamadas[0][0], "authenticate")
        # Con la autorización rota, lo dice y no intenta conectar.
        salidas.clear()
        with patch.object(oauth, "token_de_acceso", side_effect=oauth.ErrorOAuth("Google ha invalidado la autorización")):
            ok = diagnostico.probar(salida=salidas.append, smtp_factory=lambda: self.fail("no debe conectar"))
        self.assertFalse(ok)
        self.assertTrue(any("invalidado" in s for s in salidas))
        # Con contraseña (modo clásico) se usa login.
        salidas.clear()
        smtp2, imap2 = SmtpFalsoOAuth(), IMAPFalso("h")
        with patch.dict("os.environ", {"EMAIL_AUTH": "password", "EMAIL_PASSWORD": "clave"}):
            ok = diagnostico.probar(salida=salidas.append, smtp_factory=lambda: smtp2, imap_factory=lambda: imap2)
        self.assertTrue(ok)
        self.assertTrue(smtp2.login_llamado)
        self.assertEqual(imap2.llamadas[0][0], "login")


if __name__ == "__main__":
    unittest.main(verbosity=2)
