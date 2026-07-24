document.addEventListener("DOMContentLoaded", () => {
    const sidebar = document.getElementById("sidebar");
    const sidebarToggle = document.getElementById("sidebarToggle");
    if (sidebar && sidebarToggle) {
        sidebarToggle.addEventListener("click", () => sidebar.classList.toggle("open"));
    }

    document.querySelectorAll(".edit-user-btn").forEach((button) => {
        button.addEventListener("click", () => {
            document.getElementById("editUserId").value = button.dataset.id;
            document.getElementById("editLogin").value = button.dataset.login;
            document.getElementById("editRole").value = button.dataset.role;
            document.getElementById("editActive").checked = button.dataset.active === "true";
        });
    });

    document.querySelectorAll(".password-btn").forEach((button) => {
        button.addEventListener("click", () => {
            document.getElementById("passwordUserId").value = button.dataset.id;
            document.getElementById("passwordLogin").textContent = button.dataset.login;
        });
    });
});
