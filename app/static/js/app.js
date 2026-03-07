(function () {
  const root = document.documentElement;
  const savedTheme = localStorage.getItem("pdfmaster-theme");
  if (savedTheme) {
    root.setAttribute("data-bs-theme", savedTheme);
  }

  const toggle = document.querySelector("[data-theme-toggle]");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const next = root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-bs-theme", next);
      localStorage.setItem("pdfmaster-theme", next);
    });
  }

  window.setupUploadUX = function setupUploadUX() {
    const forms = document.querySelectorAll("form[enctype='multipart/form-data']");
    if (!forms.length) {
      return;
    }

    const sizeLabel = (bytes) => {
      const value = Number(bytes || 0);
      if (!Number.isFinite(value) || value <= 0) return "0 B";
      const units = ["B", "KB", "MB", "GB"];
      let size = value;
      let index = 0;
      while (size >= 1024 && index < units.length - 1) {
        size /= 1024;
        index += 1;
      }
      return `${size.toFixed(index === 0 ? 0 : 2)} ${units[index]}`;
    };

    forms.forEach((form) => {
      if (form.dataset.uploadUxReady === "1") {
        return;
      }
      const fileInputs = Array.from(form.querySelectorAll("input[type='file']"));
      if (!fileInputs.length) {
        return;
      }
      const primaryInput = fileInputs[0];
      form.dataset.uploadUxReady = "1";

      const assist = document.createElement("div");
      assist.className = "upload-assist";
      assist.innerHTML = `
        <button type="button" class="upload-drop-zone w-100 text-start">
          <strong>Drag & drop files here</strong>
          <small>or click to browse from your device</small>
        </button>
        <div class="upload-file-list">No file selected yet.</div>
        <div class="upload-progress d-none">
          <div class="progress rounded-pill">
            <div class="progress-bar bg-success"></div>
          </div>
          <div class="small text-secondary mt-1 upload-progress-text">Preparing upload...</div>
        </div>
      `;
      primaryInput.insertAdjacentElement("afterend", assist);
      const dropZone = assist.querySelector(".upload-drop-zone");
      const fileList = assist.querySelector(".upload-file-list");
      const progressWrap = assist.querySelector(".upload-progress");
      const progressBar = assist.querySelector(".progress-bar");
      const progressText = assist.querySelector(".upload-progress-text");

      const collectFiles = () => {
        const results = [];
        fileInputs.forEach((input) => {
          const entries = Array.from(input.files || []);
          entries.forEach((file) => {
            results.push({ file, field: input.name || "file" });
          });
        });
        return results;
      };

      const refreshFileList = () => {
        const files = collectFiles();
        if (!files.length) {
          fileList.textContent = "No file selected yet.";
          return;
        }
        const totalBytes = files.reduce((sum, row) => sum + (row.file.size || 0), 0);
        const preview = files
          .slice(0, 4)
          .map((row) => row.file.name)
          .join(", ");
        const extra = files.length > 4 ? ` +${files.length - 4} more` : "";
        fileList.textContent = `${files.length} file(s), ${sizeLabel(totalBytes)}: ${preview}${extra}`;
      };

      const applyDroppedFiles = (files) => {
        const list = Array.from(files || []);
        if (!list.length) {
          return;
        }
        try {
          const transfer = new DataTransfer();
          list.forEach((file) => transfer.items.add(file));
          if (primaryInput.multiple) {
            primaryInput.files = transfer.files;
          } else {
            const one = new DataTransfer();
            one.items.add(list[0]);
            primaryInput.files = one.files;
          }
          primaryInput.dispatchEvent(new Event("change", { bubbles: true }));
        } catch (error) {
          // Some older browsers lock FileList assignment; fallback is native picker.
          primaryInput.click();
        }
      };

      dropZone.addEventListener("click", () => primaryInput.click());
      ["dragenter", "dragover"].forEach((eventName) => {
        dropZone.addEventListener(eventName, (event) => {
          event.preventDefault();
          event.stopPropagation();
          dropZone.classList.add("is-dragging");
        });
      });
      ["dragleave", "drop"].forEach((eventName) => {
        dropZone.addEventListener(eventName, (event) => {
          event.preventDefault();
          event.stopPropagation();
          dropZone.classList.remove("is-dragging");
        });
      });
      dropZone.addEventListener("drop", (event) => {
        const files = event.dataTransfer && event.dataTransfer.files;
        applyDroppedFiles(files);
      });

      fileInputs.forEach((input) => {
        input.addEventListener("change", refreshFileList);
      });
      refreshFileList();

      form.addEventListener("submit", () => {
        const selectedFiles = collectFiles();
        if (!selectedFiles.length) {
          return;
        }
        progressWrap.classList.remove("d-none");
        let progressValue = 12;
        progressBar.style.width = `${progressValue}%`;
        progressText.textContent = "Uploading files...";

        const submitButtons = form.querySelectorAll("button[type='submit']");
        submitButtons.forEach((button) => {
          button.disabled = true;
        });

        const timer = window.setInterval(() => {
          progressValue = Math.min(progressValue + Math.random() * 10, 92);
          progressBar.style.width = `${progressValue}%`;
        }, 180);

        window.addEventListener(
          "beforeunload",
          () => {
            window.clearInterval(timer);
          },
          { once: true }
        );
      });
    });
  };

  window.setupSignaturePad = function setupSignaturePad() {
    const canvas = document.getElementById("signaturePad");
    if (!canvas) {
      return;
    }

    const ratio = window.devicePixelRatio || 1;
    const bounds = canvas.getBoundingClientRect();
    canvas.width = bounds.width * ratio;
    canvas.height = bounds.height * ratio;
    const ctx = canvas.getContext("2d");
    ctx.scale(ratio, ratio);
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#166d50";

    let drawing = false;
    const draw = (event) => {
      if (!drawing) return;
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      ctx.lineTo(x, y);
      ctx.stroke();
    };

    const start = (event) => {
      drawing = true;
      const rect = canvas.getBoundingClientRect();
      ctx.beginPath();
      ctx.moveTo(event.clientX - rect.left, event.clientY - rect.top);
    };

    canvas.addEventListener("pointerdown", start);
    canvas.addEventListener("pointermove", draw);
    canvas.addEventListener("pointerup", () => {
      drawing = false;
    });
    canvas.addEventListener("pointerleave", () => {
      drawing = false;
    });

    const clearButton = document.getElementById("clearSignature");
    if (clearButton) {
      clearButton.addEventListener("click", () => {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      });
    }

    const form = document.getElementById("signatureForm");
    if (form) {
      form.addEventListener("submit", () => {
        const signatureData = document.getElementById("signatureData");
        const placements = document.getElementById("placementsJson");
        const x = Number(document.getElementById("sigX").value || 320);
        const y = Number(document.getElementById("sigY").value || 60);
        const width = Number(document.getElementById("sigWidth").value || 160);
        const height = Number(document.getElementById("sigHeight").value || 64);
        const page = Number(document.getElementById("sigPage").value || 1);
        if (signatureData) {
          signatureData.value = canvas.toDataURL("image/png");
        }
        if (placements) {
          placements.value = JSON.stringify([{ page, x, y, width, height }]);
        }
      });
    }
  };

  window.setupEditorBoard = function setupEditorBoard() {
    const board = document.querySelector("[data-editor-board]");
    const form = document.getElementById("pdfEditorForm");
    const hidden = document.getElementById("editorActions");
    if (!board || !form || !hidden) {
      return;
    }

    const actionStack = [];
    const boardWidth = 595;
    const boardHeight = 842;

    const toPdfCoordinates = (x, y, width, height) => {
      const rect = board.getBoundingClientRect();
      const scaleX = boardWidth / rect.width;
      const scaleY = boardHeight / rect.height;
      return {
        x: Math.max(0, x * scaleX),
        y: Math.max(0, (rect.height - (y + height)) * scaleY),
        width: Math.max(2, width * scaleX),
        height: Math.max(2, height * scaleY),
      };
    };

    const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

    const makeDraggable = (element, action) => {
      let dragging = false;
      let startX = 0;
      let startY = 0;
      let originX = action.left;
      let originY = action.top;

      element.addEventListener("pointerdown", (event) => {
        dragging = true;
        startX = event.clientX;
        startY = event.clientY;
        originX = action.left;
        originY = action.top;
        element.setPointerCapture(event.pointerId);
      });

      element.addEventListener("pointermove", (event) => {
        if (!dragging) return;
        const nextX = clamp(originX + (event.clientX - startX), 0, board.clientWidth - action.width);
        const nextY = clamp(originY + (event.clientY - startY), 0, board.clientHeight - action.height);
        action.left = nextX;
        action.top = nextY;
        element.style.left = `${nextX}px`;
        element.style.top = `${nextY}px`;
      });

      const stop = () => {
        dragging = false;
      };
      element.addEventListener("pointerup", stop);
      element.addEventListener("pointercancel", stop);
      element.addEventListener("pointerleave", stop);
    };

    const renderAction = (action) => {
      const item = document.createElement("div");
      item.className = `editor-item editor-item-${action.type}`;
      item.style.left = `${action.left}px`;
      item.style.top = `${action.top}px`;
      item.style.width = `${action.width}px`;
      item.style.height = `${action.height}px`;
      if (action.type === "text") {
        item.textContent = action.text || "Text";
      }
      if (action.type === "line") {
        item.innerHTML = "<span></span>";
      }
      board.appendChild(item);
      makeDraggable(item, action);
      action.element = item;
    };

    const createAction = (type) => {
      if (type === "text") {
        const text = window.prompt("Enter text", "Sample text");
        if (!text) return;
        return {
          type,
          text,
          left: 56,
          top: 80,
          width: 170,
          height: 32,
          fontSize: 14,
        };
      }
      if (type === "line") {
        return {
          type,
          left: 56,
          top: 150,
          width: 180,
          height: 8,
        };
      }
      return {
        type: "rect",
        left: 56,
        top: 210,
        width: 180,
        height: 80,
      };
    };

    document.querySelectorAll("[data-editor-add]").forEach((button) => {
      button.addEventListener("click", () => {
        const type = button.getAttribute("data-editor-add");
        const action = createAction(type || "text");
        if (!action) return;
        actionStack.push(action);
        renderAction(action);
      });
    });

    const undoButton = document.querySelector("[data-editor-clear]");
    if (undoButton) {
      undoButton.addEventListener("click", () => {
        const action = actionStack.pop();
        if (!action || !action.element) return;
        action.element.remove();
      });
    }

    form.addEventListener("submit", () => {
      const payload = actionStack.map((action) => {
        const mapped = toPdfCoordinates(action.left, action.top, action.width, action.height);
        if (action.type === "text") {
          return {
            type: "text",
            page: 1,
            text: action.text,
            x: mapped.x,
            y: mapped.y + mapped.height,
            font_size: action.fontSize || 14,
          };
        }
        if (action.type === "line") {
          return {
            type: "line",
            page: 1,
            x1: mapped.x,
            y1: mapped.y + mapped.height / 2,
            x2: mapped.x + mapped.width,
            y2: mapped.y + mapped.height / 2,
            width: 2,
          };
        }
        return {
          type: "rect",
          page: 1,
          x: mapped.x,
          y: mapped.y,
          width: mapped.width,
          height: mapped.height,
          stroke_width: 2,
        };
      });
      hidden.value = JSON.stringify(payload);
    });
  };

  window.setupImageUtilityActions = function setupImageUtilityActions() {
    const form = document.getElementById("imageUtilityForm");
    if (!form) {
      return;
    }

    const actionField = document.getElementById("imageAction");
    const presetField = document.getElementById("imagePreset");
    const formatField = document.getElementById("imageTargetFormat");
    const unitField = document.getElementById("imageUnit");
    const targetKbField = document.getElementById("imageTargetKb");
    const sizeFromField = form.querySelector("select[name='size_from']");
    const sizeToField = form.querySelector("select[name='size_to']");

    const applyAction = (button, submitNow) => {
      const nextAction = button.getAttribute("data-image-action");
      if (nextAction && actionField) {
        actionField.value = nextAction;
      }
      if (presetField) {
        presetField.value = button.getAttribute("data-preset") || "";
      }
      const targetFormat = button.getAttribute("data-target-format");
      if (targetFormat && formatField) {
        formatField.value = targetFormat;
      }
      const nextUnit = button.getAttribute("data-unit");
      if (nextUnit && unitField) {
        unitField.value = nextUnit;
      }
      const nextTargetKb = button.getAttribute("data-target-kb");
      if (nextTargetKb && targetKbField) {
        targetKbField.value = nextTargetKb;
      }
      const nextSizeFrom = button.getAttribute("data-size-from");
      if (nextSizeFrom && sizeFromField) {
        sizeFromField.value = nextSizeFrom;
      }
      const nextSizeTo = button.getAttribute("data-size-to");
      if (nextSizeTo && sizeToField) {
        sizeToField.value = nextSizeTo;
      }

      if (submitNow) {
        form.requestSubmit();
      }
    };

    const actionButtons = form.querySelectorAll("[data-image-action]");
    actionButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const submitNow = button.classList.contains("image-action-tile");
        applyAction(button, submitNow);
      });
    });

    const search = document.getElementById("imageToolSearch");
    if (search) {
      const sections = form.querySelectorAll(".image-tool-section");
      search.addEventListener("input", () => {
        const token = (search.value || "").trim().toLowerCase();
        sections.forEach((section) => {
          let visibleCount = 0;
          section.querySelectorAll(".image-action-tile").forEach((button) => {
            const text = (button.textContent || "").toLowerCase();
            const visible = !token || text.includes(token);
            button.classList.toggle("d-none", !visible);
            if (visible) visibleCount += 1;
          });
          section.classList.toggle("d-none", visibleCount === 0);
        });
      });
    }
  };

  window.setupPdfCompressActions = function setupPdfCompressActions() {
    const form = document.getElementById("pdfCompressForm");
    if (!form) {
      return;
    }
    const actionField = document.getElementById("compressAction");
    const targetField = document.getElementById("compressTargetKb");
    const buttons = form.querySelectorAll("[data-pdf-action]");

    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        const nextAction = button.getAttribute("data-pdf-action") || "level";
        if (actionField) {
          actionField.value = nextAction;
        }
        const targetKb = button.getAttribute("data-target-kb");
        if (targetKb && targetField) {
          targetField.value = targetKb;
        }

        const shouldSubmit = button.getAttribute("data-submit") === "true" || Boolean(targetKb);
        if (shouldSubmit) {
          form.requestSubmit();
        }
      });
    });

    form.addEventListener("submit", () => {
      if (!actionField || actionField.value) {
        return;
      }
      actionField.value = "level";
    });
  };

  window.setupAllToolsCatalog = function setupAllToolsCatalog() {
    const root = document.getElementById("allToolsCatalog");
    if (!root) {
      return;
    }
    if (root.dataset.catalogReady === "1") {
      return;
    }
    root.dataset.catalogReady = "1";

    const groups = Array.from(root.querySelectorAll("[data-tool-group]"));
    if (!groups.length) {
      return;
    }

    const searchInput = document.getElementById("toolCatalogSearch");
    const categorySelect = document.getElementById("toolCatalogCategory");
    const tierSelect = document.getElementById("toolCatalogTier");
    const onlyFavoritesInput = document.getElementById("toolCatalogOnlyFavorites");
    const emptyState = document.getElementById("toolCatalogEmpty");
    const showAllButton = document.querySelector("[data-catalog-show='all']");
    const collapseAllButton = document.querySelector("[data-catalog-show='collapse']");
    const jumpButtons = document.querySelectorAll("[data-group-jump]");
    const singleOpenMedia = window.matchMedia("(max-width: 991.98px)");

    const isSingleOpenMode = () => singleOpenMedia.matches;

    const ensureRendered = (group) => {
      if (!group || group.dataset.rendered === "1") {
        return;
      }
      const body = group.querySelector("[data-group-body]");
      const templateId = group.getAttribute("data-template-id") || "";
      const template = templateId ? document.getElementById(templateId) : null;
      if (!body || !template) {
        group.dataset.rendered = "1";
        return;
      }
      body.innerHTML = "";
      body.appendChild(template.content.cloneNode(true));
      group.dataset.rendered = "1";
    };

    const setPanelHeight = (group, open) => {
      const panel = group.querySelector("[data-group-panel]");
      if (!panel) {
        return;
      }
      if (open) {
        panel.style.maxHeight = `${panel.scrollHeight}px`;
      } else {
        panel.style.maxHeight = "0px";
      }
    };

    const updateGroupCount = (group, count) => {
      const countNode = group.querySelector("[data-group-visible-count]");
      if (countNode) {
        countNode.textContent = String(count);
      }
    };

    const closeGroup = (group) => {
      if (!group || !group.classList.contains("is-open")) {
        return;
      }
      group.classList.remove("is-open");
      const toggle = group.querySelector("[data-group-toggle]");
      const panel = group.querySelector("[data-group-panel]");
      if (toggle) {
        toggle.setAttribute("aria-expanded", "false");
      }
      if (panel) {
        panel.setAttribute("aria-hidden", "true");
      }
      setPanelHeight(group, false);
    };

    const openGroup = (group, { scroll = false } = {}) => {
      if (!group) {
        return;
      }
      ensureRendered(group);
      if (isSingleOpenMode()) {
        groups.forEach((row) => {
          if (row !== group) {
            closeGroup(row);
          }
        });
      }
      group.classList.add("is-open");
      const toggle = group.querySelector("[data-group-toggle]");
      const panel = group.querySelector("[data-group-panel]");
      if (toggle) {
        toggle.setAttribute("aria-expanded", "true");
      }
      if (panel) {
        panel.setAttribute("aria-hidden", "false");
      }
      setPanelHeight(group, true);
      if (scroll) {
        group.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    };

    const toggleGroup = (group) => {
      if (!group) {
        return;
      }
      if (group.classList.contains("is-open")) {
        closeGroup(group);
      } else {
        openGroup(group);
      }
    };

    const matchesCardFilters = (card, filters) => {
      const searchToken = filters.searchToken;
      const onlyFavorites = filters.onlyFavorites;
      const tier = filters.tier;

      const nameToken = (card.getAttribute("data-tool-name") || "").toLowerCase();
      const categoryToken = (card.getAttribute("data-tool-category") || "").toLowerCase();
      const tierToken = (card.getAttribute("data-tool-tier") || "free").toLowerCase();
      const isFavorite = card.getAttribute("data-tool-favorite") === "1";

      const searchMatch = !searchToken || nameToken.includes(searchToken) || categoryToken.includes(searchToken);
      const tierMatch = tier === "all" || tierToken === tier;
      const favoritesMatch = !onlyFavorites || isFavorite;
      return searchMatch && tierMatch && favoritesMatch;
    };

    const applyFilters = () => {
      const filters = {
        searchToken: (searchInput && searchInput.value ? searchInput.value : "").trim().toLowerCase(),
        selectedCategory: (categorySelect && categorySelect.value ? categorySelect.value : "all").trim(),
        tier: (tierSelect && tierSelect.value ? tierSelect.value : "all").trim(),
        onlyFavorites: Boolean(onlyFavoritesInput && onlyFavoritesInput.checked),
      };
      const hasCardFilters = Boolean(filters.searchToken || filters.tier !== "all" || filters.onlyFavorites);

      let visibleGroups = 0;
      groups.forEach((group) => {
        const groupId = group.getAttribute("data-group-id") || "";
        const totalCount = Number.parseInt(group.getAttribute("data-total-count") || "0", 10) || 0;
        const categoryMatch = filters.selectedCategory === "all" || filters.selectedCategory === groupId;
        if (!categoryMatch) {
          group.classList.add("d-none");
          closeGroup(group);
          return;
        }

        if (!hasCardFilters) {
          if (group.dataset.rendered === "1") {
            group.querySelectorAll("[data-tool-card].d-none").forEach((card) => {
              card.classList.remove("d-none");
            });
          }
          group.classList.remove("d-none");
          updateGroupCount(group, totalCount);
          if (group.classList.contains("is-open")) {
            setPanelHeight(group, true);
          }
          visibleGroups += 1;
          return;
        }

        ensureRendered(group);
        const cards = Array.from(group.querySelectorAll("[data-tool-card]"));
        let visibleCards = 0;
        cards.forEach((card) => {
          const isVisible = matchesCardFilters(card, filters);
          card.classList.toggle("d-none", !isVisible);
          if (isVisible) {
            visibleCards += 1;
          }
        });
        updateGroupCount(group, visibleCards);
        const groupVisible = visibleCards > 0;
        group.classList.toggle("d-none", !groupVisible);
        if (!groupVisible) {
          closeGroup(group);
          return;
        }
        visibleGroups += 1;
        if (group.classList.contains("is-open")) {
          setPanelHeight(group, true);
        }
      });

      if (emptyState) {
        emptyState.classList.toggle("d-none", visibleGroups > 0);
      }

      if (filters.selectedCategory !== "all") {
        const selectedGroup = groups.find((group) => group.getAttribute("data-group-id") === filters.selectedCategory);
        if (selectedGroup && !selectedGroup.classList.contains("d-none")) {
          openGroup(selectedGroup);
        }
      }
    };

    groups.forEach((group) => {
      const isDefaultOpen = group.getAttribute("data-default-open") === "1";
      const panel = group.querySelector("[data-group-panel]");
      if (panel) {
        panel.setAttribute("aria-hidden", isDefaultOpen ? "false" : "true");
      }
      if (!isDefaultOpen) {
        closeGroup(group);
      } else {
        ensureRendered(group);
        openGroup(group);
      }

      const toggle = group.querySelector("[data-group-toggle]");
      if (toggle) {
        toggle.addEventListener("click", () => {
          toggleGroup(group);
        });
      }
    });

    if (searchInput) {
      searchInput.addEventListener("input", applyFilters);
    }
    if (categorySelect) {
      categorySelect.addEventListener("change", applyFilters);
    }
    if (tierSelect) {
      tierSelect.addEventListener("change", applyFilters);
    }
    if (onlyFavoritesInput) {
      onlyFavoritesInput.addEventListener("change", applyFilters);
    }

    if (showAllButton) {
      showAllButton.addEventListener("click", () => {
        if (isSingleOpenMode()) {
          const firstVisible = groups.find((group) => !group.classList.contains("d-none"));
          if (firstVisible) {
            openGroup(firstVisible, { scroll: true });
          }
          return;
        }
        groups.forEach((group) => {
          if (group.classList.contains("d-none")) {
            return;
          }
          ensureRendered(group);
          openGroup(group);
        });
      });
    }

    if (collapseAllButton) {
      collapseAllButton.addEventListener("click", () => {
        groups.forEach((group) => closeGroup(group));
      });
    }

    jumpButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const targetId = button.getAttribute("data-group-jump") || "";
        const targetGroup = groups.find((group) => group.getAttribute("data-group-id") === targetId);
        if (!targetGroup) {
          return;
        }
        if (categorySelect && categorySelect.value !== "all" && categorySelect.value !== targetId) {
          categorySelect.value = "all";
          applyFilters();
        }
        openGroup(targetGroup, { scroll: true });
      });
    });

    singleOpenMedia.addEventListener("change", () => {
      if (!isSingleOpenMode()) {
        return;
      }
      const openVisible = groups.filter(
        (group) => group.classList.contains("is-open") && !group.classList.contains("d-none")
      );
      if (openVisible.length <= 1) {
        return;
      }
      openVisible.slice(1).forEach((group) => closeGroup(group));
    });

    applyFilters();
  };

  window.setupPremiumBillingUI = function setupPremiumBillingUI() {
    const billingSection = document.getElementById("billing");
    if (!billingSection) {
      return;
    }
    if (billingSection.dataset.billingUiReady === "1") {
      return;
    }
    billingSection.dataset.billingUiReady = "1";

    const planCards = Array.from(billingSection.querySelectorAll("[data-plan-card]"));
    if (!planCards.length) {
      return;
    }
    const storageKey = "pdfmaster-selected-plan";
    const selectPlan = (planKey, options = {}) => {
      if (!planKey) {
        return;
      }
      planCards.forEach((card) => {
        const isMatch = card.getAttribute("data-plan-key") === planKey;
        card.classList.toggle("is-selected", isMatch);
      });
      if (options.persist !== false) {
        try {
          localStorage.setItem(storageKey, planKey);
        } catch (error) {
          // Ignore storage errors in privacy-restricted contexts.
        }
      }
      if (options.scroll) {
        const target = planCards.find((card) => card.getAttribute("data-plan-key") === planKey);
        target && target.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    };

    planCards.forEach((card) => {
      const planKey = card.getAttribute("data-plan-key");
      if (!planKey) {
        return;
      }
      card.addEventListener("click", () => {
        selectPlan(planKey);
      });
      const actionButton = card.querySelector("[data-upgrade-button]");
      if (actionButton) {
        actionButton.addEventListener("focus", () => {
          selectPlan(planKey);
        });
      }
    });

    const focusButtons = billingSection.querySelectorAll("[data-plan-focus]");
    focusButtons.forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        const planKey = button.getAttribute("data-plan-focus");
        selectPlan(planKey, { scroll: true });
        const hashTarget = document.getElementById("billing-plans");
        hashTarget && hashTarget.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });

    let initialPlanKey = "";
    try {
      initialPlanKey = localStorage.getItem(storageKey) || "";
    } catch (error) {
      initialPlanKey = "";
    }
    if (!initialPlanKey || !planCards.some((card) => card.getAttribute("data-plan-key") === initialPlanKey)) {
      const recommended = planCards.find((card) => card.getAttribute("data-plan-recommended") === "true");
      const activeCard = planCards.find((card) => card.classList.contains("is-active-plan"));
      initialPlanKey = (activeCard && activeCard.getAttribute("data-plan-key")) || (recommended && recommended.getAttribute("data-plan-key")) || planCards[0].getAttribute("data-plan-key");
    }
    selectPlan(initialPlanKey, { persist: false });

    const customForm = billingSection.querySelector("form.razorpay-upgrade-form[data-custom-plan='true']");
    if (!customForm) {
      return;
    }
    const customCard = customForm.closest("[data-plan-card]");
    const customDaysInput = customForm.querySelector("input[name='custom_days']");
    const customError = customForm.querySelector("[data-custom-error]");
    const customPriceDisplay = customForm.querySelector("[data-custom-price-display]");
    const customPriceText = customForm.querySelector("[data-custom-price]");
    const customDaysLiveText = customForm.querySelector("[data-custom-live-days]");
    const customConfirmText = customForm.querySelector("[data-custom-confirm]");
    const customSubmitButton = customForm.querySelector("[data-upgrade-button]");
    const quickChips = customCard ? customCard.querySelectorAll("[data-custom-days-chip]") : [];
    if (!customDaysInput || !customSubmitButton) {
      return;
    }

    const minDays = Number.parseInt(customDaysInput.getAttribute("min") || "1", 10);
    const maxDays = Number.parseInt(customDaysInput.getAttribute("max") || "365", 10);
    const dailyRatePaise = Number.parseInt(customForm.getAttribute("data-daily-rate-paise") || "100", 10);
    const formatRupees = (value) => {
      return Number.isInteger(value) ? `₹${value}` : `₹${value.toFixed(2)}`;
    };

    const parseCustomDays = () => {
      const sanitized = (customDaysInput.value || "").replace(/[^\d]/g, "");
      if (customDaysInput.value !== sanitized) {
        customDaysInput.value = sanitized;
      }
      if (!sanitized) {
        return { valid: false, message: "", days: 0 };
      }
      const parsedDays = Number.parseInt(sanitized, 10);
      if (!Number.isFinite(parsedDays)) {
        return { valid: false, message: "Please enter a valid number.", days: 0 };
      }
      if (parsedDays < minDays || parsedDays > maxDays) {
        return {
          valid: false,
          message: `Enter days between ${minDays} and ${maxDays}.`,
          days: parsedDays,
        };
      }
      return { valid: true, message: "", days: parsedDays };
    };

    const renderCustomPreview = () => {
      const parsed = parseCustomDays();
      const fallbackDays = minDays;
      const previewDays = parsed.valid ? parsed.days : (parsed.days || fallbackDays);
      const amount = previewDays * (dailyRatePaise / 100);
      if (customPriceDisplay) {
        customPriceDisplay.classList.remove("d-none");
      }
      if (customDaysLiveText) {
        customDaysLiveText.textContent = String(previewDays);
      }
      if (customPriceText) {
        customPriceText.textContent = formatRupees(amount);
      }
      if (customConfirmText) {
        customConfirmText.textContent = `Final payable amount: ${formatRupees(amount)} for ${previewDays} day${previewDays === 1 ? "" : "s"}.`;
      }
      customSubmitButton.disabled = !parsed.valid;
      customDaysInput.setAttribute("aria-invalid", parsed.valid ? "false" : "true");
      if (customError) {
        const showError = Boolean(parsed.message && customDaysInput.value.trim());
        customError.classList.toggle("d-none", !showError);
        customError.textContent = showError ? parsed.message : "";
      }
      return parsed;
    };

    quickChips.forEach((chip) => {
      chip.addEventListener("click", () => {
        const nextDays = chip.getAttribute("data-custom-days-chip") || "";
        customDaysInput.value = nextDays;
        renderCustomPreview();
        selectPlan("pro_custom");
      });
    });

    customDaysInput.addEventListener("focus", () => {
      selectPlan("pro_custom");
    });
    customDaysInput.addEventListener("input", () => {
      renderCustomPreview();
      if (customDaysInput.value.trim()) {
        selectPlan("pro_custom");
      }
    });

    renderCustomPreview();
  };

  window.setupRazorpayUpgrade = function setupRazorpayUpgrade() {
    const forms = document.querySelectorAll("form.razorpay-upgrade-form");
    if (!forms.length) {
      return;
    }

    const toStatusUrl = (message) => {
      const fallback = "/billing/status?state=failed&message=";
      return `${fallback}${encodeURIComponent(message || "Payment could not be started.")}`;
    };

    const formatRupees = (value) => {
      return Number.isInteger(value) ? `₹${value}` : `₹${value.toFixed(2)}`;
    };

    forms.forEach((form) => {
      if (form.dataset.razorpayBound === "1") {
        return;
      }
      form.dataset.razorpayBound = "1";

      const button = form.querySelector("[data-upgrade-button]");
      if (!button) {
        return;
      }
      const defaultLabel = button.getAttribute("data-default-label") || (button.textContent || "").trim() || "Activate plan";
      const customDaysInput = form.querySelector("input[name='custom_days']");
      const customError = form.querySelector("[data-custom-error]");
      const dailyRatePaise = Number.parseInt(form.getAttribute("data-daily-rate-paise") || "100", 10);
      const minDays = Number.parseInt((customDaysInput && customDaysInput.getAttribute("min")) || "1", 10);
      const maxDays = Number.parseInt((customDaysInput && customDaysInput.getAttribute("max")) || "3650", 10);
      let inFlight = false;

      const setButtonState = (state, label) => {
        if (state === "loading") {
          button.disabled = true;
          button.classList.add("is-loading");
          button.textContent = label || "Opening checkout...";
          return;
        }
        button.classList.remove("is-loading");
        if (state === "success") {
          button.disabled = true;
          button.classList.add("btn-success");
          button.textContent = label || "Checkout opened";
          return;
        }
        button.classList.remove("btn-success");
        button.textContent = defaultLabel;
        if (!customDaysInput) {
          button.disabled = false;
          return;
        }
        const parsed = parseCustomDays();
        button.disabled = !parsed.valid;
      };

      const parseCustomDays = () => {
        if (!customDaysInput) {
          return { valid: true, days: null, message: "" };
        }
        const sanitized = (customDaysInput.value || "").replace(/[^\d]/g, "");
        if (customDaysInput.value !== sanitized) {
          customDaysInput.value = sanitized;
        }
        if (!sanitized) {
          return { valid: false, days: null, message: `Enter days between ${minDays} and ${maxDays}.` };
        }
        const parsed = Number.parseInt(sanitized, 10);
        if (!Number.isFinite(parsed)) {
          return { valid: false, days: null, message: "Please enter a valid number of days." };
        }
        if (parsed < minDays || parsed > maxDays) {
          return { valid: false, days: parsed, message: `Enter days between ${minDays} and ${maxDays}.` };
        }
        return { valid: true, days: parsed, message: "" };
      };

      if (customDaysInput) {
        customDaysInput.addEventListener("input", () => {
          const parsed = parseCustomDays();
          if (customError) {
            const showError = Boolean(parsed.message && customDaysInput.value.trim());
            customError.classList.toggle("d-none", !showError);
            customError.textContent = showError ? parsed.message : "";
          }
          button.disabled = !parsed.valid;
        });
      }

      setButtonState("idle");

      button.addEventListener("click", async () => {
        if (inFlight) {
          return;
        }
        const submitUrl = form.getAttribute("action");
        const csrf = (form.querySelector("input[name='csrf_token']") || {}).value || "";
        const planKey = (form.querySelector("input[name='plan_key']") || {}).value || "";
        let customDaysValue = null;
        if (!submitUrl || !planKey) {
          window.location.href = toStatusUrl("Plan details are missing.");
          return;
        }

        if (customDaysInput) {
          const parsed = parseCustomDays();
          if (!parsed.valid) {
            if (customError) {
              customError.classList.remove("d-none");
              customError.textContent = parsed.message;
            }
            customDaysInput.focus();
            return;
          }
          customDaysValue = parsed.days;
          if (customError) {
            customError.classList.add("d-none");
          }
        }

        if (typeof window.Razorpay === "undefined") {
          window.location.href = toStatusUrl("Razorpay checkout failed to load.");
          return;
        }

        inFlight = true;
        setButtonState("loading", "Opening checkout...");
        let checkoutOpened = false;
        try {
          const body = new URLSearchParams();
          body.append("plan_key", planKey);
          if (customDaysValue !== null) {
            body.append("custom_days", String(customDaysValue));
          }
          body.append("csrf_token", csrf);

          const response = await fetch(submitUrl, {
            method: "POST",
            headers: {
              "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
              "X-Requested-With": "XMLHttpRequest",
            },
            body: body.toString(),
          });
          const payload = await response.json();
          if (!response.ok) {
            throw new Error(payload.error || "Unable to create payment order.");
          }

          const checkout = new window.Razorpay({
            key: payload.key_id,
            amount: payload.amount,
            currency: payload.currency,
            name: payload.app_name || "PDFMaster Ultra Suite",
            description: `${payload.plan_name || "Premium"} Subscription`,
            order_id: payload.order_id,
            callback_url: payload.callback_url,
            prefill: payload.prefill || {},
            notes: payload.notes || {},
            theme: {
              color: "#166d50",
            },
            modal: {
              ondismiss: function onDismiss() {
                const fallbackMessage = customDaysValue
                  ? `Payment cancelled for ${customDaysValue} days (${formatRupees(customDaysValue * (dailyRatePaise / 100))}).`
                  : "Payment was cancelled.";
                window.location.href = payload.status_url_on_dismiss || toStatusUrl(fallbackMessage);
              },
            },
          });
          checkout.open();
          checkoutOpened = true;
        } catch (error) {
          const message = (error && error.message) || "Payment could not be started.";
          window.location.href = toStatusUrl(message);
          return;
        } finally {
          inFlight = false;
          if (checkoutOpened) {
            setButtonState("success", "Checkout opened");
            window.setTimeout(() => {
              setButtonState("idle");
            }, 1300);
          } else {
            setButtonState("idle");
          }
        }
      });
    });
  };

  const defaultBillingSettingsUrl = "https://pdf-master-ultra-suite.onrender.com/settings?tab=billing";

  window.getBillingSettingsUrl = function getBillingSettingsUrl() {
    const configuredUrl = (document.body && document.body.dataset && document.body.dataset.billingSettingsUrl) || "";
    return configuredUrl || defaultBillingSettingsUrl;
  };

  window.redirectToBillingSettings = function redirectToBillingSettings(event) {
    if (event && typeof event.preventDefault === "function") {
      event.preventDefault();
    }
    window.location.assign(window.getBillingSettingsUrl());
    return false;
  };

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-billing-redirect]");
    if (!trigger) {
      return;
    }
    window.redirectToBillingSettings(event);
  });

  window.setupUploadUX && window.setupUploadUX();
})();
