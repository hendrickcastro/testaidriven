- Lenguaje de Programacion Python.
- Crea un programa en python con ui.
- Mejores  practicas de desarrollo
- Bateria configurable de pruebas
- Repositorio de artefactos, local y/o en la nube configurable.
- conexion a firestore para guardar datos relevantes.
- usar prefijo aidriven_ en todas las colecciones que use este proyecto.
- La bateria configurable de pruebas debe ser perfectamente configurable entre modelos, effort, modelo, razonamiento si lo tiene vision etc todo lo que un modelo pueda soportar
- los resultados de las pruebas deben ser guardaos y podemos compararlos con respecto a otros,
- los resultados deben poder ser comparables por tarea y por conjunto en general contra otra prueba si lo hubiere.
- La bateria de prueba inicial debe montar tareas complejas.
- se deben capturar todo, tokens (cantidad input, cantidad de output), tiempo, certeza o aceptacion, es decir las tareas deben otorgar un score del 1 al 10 para que el usuario lo puntue, pero un modelo modelo configurado superior (este modelo debe ser configurable desde la UI), puede puntuar previamente como preview pudiendo corregir el usuario.
Los modelos deben ser completamente configurables desde un aparatado de configuracion, puedes usar de ejemplo a D:\_ALGORITXIA\AI-Research\LLM\ContextAdmin.
- podemos incluir rules agents, etc en la configuración, para que estos sean aplicados en la bateria de test.
- debes buscar tareas predeterminadas superdificiles para las ai actuales, cuidando que los guardarailes de los llm no los bloquee o se niegue a contestar.
- debo poder agregar todas las tareas que quiera.
los artefactos deben ser capaz de poder reproducirse en un ambiente controlado, por ejemplo la ejecucion de un programa, la ejecucion de un html etc, embebiendo esta ejecucion para que no altere el host donde se corre el programa principal.
- El objetivo es poder medir la fiabilidad, performance, conformidad e inteligencia entre modelos
- si necesitas saber algo mas pregunta.
- usa la arquitectura de desarrollo specdriven como aqui D:\_ALGORITXIA\AI-Research\LLM\LyricSearch\specs