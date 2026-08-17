import ctranslate2

from faster_whisper import WhisperModel


print("=" * 50)
print("Проверка GPU")
print("=" * 50)

gpu_count = ctranslate2.get_cuda_device_count()

print("Найдено CUDA GPU:", gpu_count)

if gpu_count == 0:
    print("ОШИБКА: CUDA GPU не обнаружен")
    raise SystemExit(1)


print()
print("Загрузка Whisper на GPU...")


model = WhisperModel(
    "medium",
    device="cuda",
    compute_type="float16",
)


print()
print("Whisper успешно загружен!")
print("Устройство: CUDA")
print("Compute type: float16")
print("=" * 50)