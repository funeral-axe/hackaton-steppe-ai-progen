const statusBox = document.getElementById("indexStatus");

async function refreshStatus() {
    if (!statusBox) return;
    try {
        const response = await fetch("/index-status", {cache: "no-store"});
        const data = await response.json();
        if (!data.status || data.status === "none") return;
        const status = document.getElementById("jobStatus");
        const progress = document.getElementById("jobProgress");
        const details = document.getElementById("jobDetails");
        const statusLabels = {
            queued: "В очереди",
            running: "Выполняется",
            completed: "Завершено",
            error: "Ошибка",
            cancelled: "Отменено",
            paused: "Приостановлено",
        };

        if (status) {
            status.textContent =
                statusLabels[data.status] ||
                data.status;
        }
        if (progress) progress.style.width = `${data.percent || 0}%`;
        if (details) details.textContent = `${data.processed || 0} из ${data.total || 0}; файл: ${data.current_file || "-"}; ошибок: ${data.failed || 0}`;
        if (["queued", "running"].includes(data.status)) {
            window.setTimeout(refreshStatus, 2000);
        } else if (data.status === "completed") {
            window.setTimeout(() => window.location.reload(), 1000);
        }
    } catch (error) {
        console.error("Не удалось получить статус индексации", error);
    }
}
if (statusBox?.dataset.active === "true") refreshStatus();
