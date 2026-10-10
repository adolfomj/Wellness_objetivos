// ==============================================================================
// CONFIGURACIÓN DE SUPABASE Y EQUIPO
// ==============================================================================
// Obtén estos dos valores en: https://supabase.com/dashboard
// Entra a tu proyecto -> Project Settings (icono de engranaje) -> API
// ==============================================================================

const SUPABASE_CONFIG = {
  // Reemplaza con la URL de tu proyecto Supabase (ej: 'https://abcdefghijklm.supabase.co')
  url: 'TU_SUPABASE_URL_AQUI',

  // Reemplaza con tu clave anon / public (comienza con 'eyJ...')
  anonKey: 'TU_SUPABASE_ANON_KEY_AQUI',

  // Datos del equipo
  teamName: 'Wellness Movilidad',
  storeName: 'Decathlon',
  shiftMinutes: 480,   // Jornada de 8h para medir la carga
  defaultDueHour: 19   // Hora límite por defecto (19:00)
};
