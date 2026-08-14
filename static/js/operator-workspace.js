(() => {
    const navItems = Array.from(
        document.querySelectorAll(
            "[data-operator-view]"
        )
    );

    const panels = Array.from(
        document.querySelectorAll(
            "[data-operator-panel]"
        )
    );

    const registryTab =
        document.getElementById(
            "operatorTabRegistry"
        );

    const bioTab =
        document.getElementById(
            "operatorTabBio"
        );


    function activateView(view) {
        navItems.forEach((item) => {
            item.classList.toggle(
                "active",
                item.dataset.operatorView === view
            );
        });

        panels.forEach((panel) => {
            panel.hidden =
                panel.dataset.operatorPanel !== view;
        });

        if (
            view === "registry" &&
            registryTab
        ) {
            registryTab.click();
        }

        if (
            view === "biometric" &&
            bioTab
        ) {
            bioTab.click();
        }

        try {
            sessionStorage.setItem(
                "sonarOperatorView",
                view
            );
        } catch (_) {
            // Optional browser state only.
        }
    }


    navItems.forEach((item) => {
        item.addEventListener(
            "click",
            () => {
                activateView(
                    item.dataset.operatorView
                );
            }
        );
    });


    const searchInput =
        document.getElementById(
            "searchInput"
        );

    if (searchInput) {
        searchInput.addEventListener(
            "keydown",
            (event) => {
                if (event.key !== "Enter") {
                    return;
                }

                event.preventDefault();

                if (
                    typeof window.filterBySearch
                    === "function"
                ) {
                    window.filterBySearch();
                }
            }
        );
    }


    const params =
        new URLSearchParams(
            window.location.search
        );

    let initialView = "register";

    if (params.has("search")) {
        initialView = "registry";
    } else {
        try {
            const saved =
                sessionStorage.getItem(
                    "sonarOperatorView"
                );

            if (
                [
                    "register",
                    "registry",
                    "biometric",
                ].includes(saved)
            ) {
                initialView = saved;
            }
        } catch (_) {
            // Optional browser state only.
        }
    }

    activateView(
        initialView
    );
})();
