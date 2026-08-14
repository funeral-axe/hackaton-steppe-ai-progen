(() => {
    const buttons = Array.from(
        document.querySelectorAll(
            "[data-search-view]"
        )
    );

    const panels = Array.from(
        document.querySelectorAll(
            "[data-search-panel]"
        )
    );

    if (!buttons.length || !panels.length) {
        return;
    }


    function activate(viewName) {
        buttons.forEach((button) => {
            const active =
                button.dataset.searchView === viewName;

            button.classList.toggle(
                "active",
                active
            );
        });


        panels.forEach((panel) => {
            const active =
                panel.dataset.searchPanel === viewName;

            panel.hidden = !active;
        });


        try {
            sessionStorage.setItem(
                "sonar-text-view",
                viewName
            );
        } catch (_) {
            // Storage is optional.
        }
    }


    buttons.forEach((button) => {
        button.addEventListener(
            "click",
            () => {
                activate(
                    button.dataset.searchView
                );
            }
        );
    });


    const indexStatus =
        document.getElementById(
            "indexStatus"
        );


    let initialView = "quick";


    if (
        indexStatus &&
        indexStatus.dataset.active === "true"
    ) {
        initialView = "indexing";
    } else {
        try {
            const stored = sessionStorage.getItem(
                "sonar-text-view"
            );

            if (
                stored &&
                panels.some(
                    (panel) =>
                        panel.dataset.searchPanel === stored
                )
            ) {
                initialView = stored;
            }
        } catch (_) {
            // Storage is optional.
        }
    }


    activate(
        initialView
    );
})();