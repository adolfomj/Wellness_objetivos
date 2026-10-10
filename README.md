# Objetivos del Equipo · Decathlon Wellness Movilidad (Supabase + GitHub)

Esta versión está adaptada para funcionar de manera **estática y en la nube**:
- **Frontend:** Alojado en **GitHub Pages** (o localmente con cualquier navegador).
- **Base de datos:** En la nube con **Supabase** (PostgreSQL + Realtime).
- **Dispositivos:** Celulares y computadores en cualquier red (datos móviles o Wi-Fi).

---

## Paso 1: Configurar Supabase (2 minutos)

1. Crea una cuenta gratuita en [supabase.com](https://supabase.com) y crea un nuevo proyecto (por ejemplo `decathlon-objetivos`).
2. Ve al menú lateral **SQL Editor** -> haz clic en **New query**.
3. Abre el archivo [`supabase_schema.sql`](file:///c:/Users/USER/Desktop/Pagina_objetivos/supabase_schema.sql), copia todo su contenido, pégalo en el editor y presiona **Run**. Esto creará todas las tablas y permisos necesarios.
4. Ve a **Project Settings** (icono de engranaje) -> **API** y copia:
   - **Project URL** (ejemplo: `https://abcdefghijklm.supabase.co`)
   - **Project API keys -> `anon` / `public`** (clave que empieza por `eyJ...`)
5. Abre el archivo [`public/config.js`](file:///c:/Users/USER/Desktop/Pagina_objetivos/public/config.js) y reemplaza los valores de `url` y `anonKey`.

---

## Paso 2: Subir a GitHub y activar GitHub Pages

1. Inicializa tu repositorio y súbelo a GitHub:
   ```bash
   git init
   git add .
   git commit -m "Inicializar app objetivos con Supabase"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/TU_REPOSITORIO.git
   git push -u origin main
   ```
2. En tu repositorio en GitHub:
   - Ve a **Settings** -> **Pages** (en el menú lateral izquierdo).
   - En **Build and deployment -> Source**, selecciona **Deploy from a branch**.
   - En **Branch**, selecciona `main` y en la carpeta elige `/public` (o `/root` si moviste los archivos a la raíz).
   - Haz clic en **Save**.
3. En unos segundos, GitHub te dará el enlace público de tu aplicación (ej: `https://tu_usuario.github.io/tu_repositorio/`).

¡Listo! Comparte ese enlace con el equipo por WhatsApp o el canal que usen. Al abrirlo en el celular pueden añadirlo a la pantalla de inicio como una aplicación nativa.
