(() => {
    const navButtons = Array.from(
        document.querySelectorAll(
            "[data-voice-view]"
        )
    );

    const panels = Array.from(
        document.querySelectorAll(
            "[data-voice-panel]"
        )
    );

    const progressContainer =
        document.getElementById(
            "progress-container"
        );

    const taskEmpty =
        document.getElementById(
            "voiceTaskEmpty"
        );

    const form =
        document.getElementById(
            "voiceSearchForm"
        );


    if (!navButtons.length || !panels.length) {
        return;
    }


    function activate(viewName) {
        navButtons.forEach((button) => {
            button.classList.toggle(
                "active",
                button.dataset.voiceView === viewName
            );
        });


        panels.forEach((panel) => {
            panel.hidden =
                panel.dataset.voicePanel !== viewName;
        });
    }


    function progressIsVisible() {
        if (!progressContainer) {
            return false;
        }

        return (
            progressContainer.style.display !== "none" &&
            window.getComputedStyle(
                progressContainer
            ).display !== "none"
        );
    }


    function syncTaskEmpty() {
        if (!taskEmpty) {
            return;
        }

        taskEmpty.hidden =
            progressIsVisible();
    }


    navButtons.forEach((button) => {
        button.addEventListener(
            "click",
            () => {
                activate(
                    button.dataset.voiceView
                );
            }
        );
    });


    if (form) {
        form.addEventListener(
            "submit",
            () => {
                window.setTimeout(
                    () => {
                        syncTaskEmpty();

                        if (progressIsVisible()) {
                            activate("task");
                        }
                    },
                    0
                );
            }
        );
    }


    if (progressContainer) {
        const observer =
            new MutationObserver(
                () => {
                    syncTaskEmpty();

                    if (progressIsVisible()) {
                        activate("task");
                    }
                }
            );

        observer.observe(
            progressContainer,
            {
                attributes: true,
                attributeFilter: [
                    "style",
                    "class"
                ]
            }
        );
    }


    syncTaskEmpty();
    activate("setup");
})();