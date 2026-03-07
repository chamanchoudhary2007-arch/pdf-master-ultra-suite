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

  window.setupRazorpayUpgrade = function setupRazorpayUpgrade() {
    const forms = document.querySelectorAll("form.razorpay-upgrade-form");
    if (!forms.length) {
      return;
    }

    const toStatusUrl = (message) => {
      const fallback = "/billing/status?state=failed&message=";
      return `${fallback}${encodeURIComponent(message || "Payment could not be started.")}`;
    };

    forms.forEach((form) => {
      const button = form.querySelector("[data-upgrade-button]");
      if (!button) {
        return;
      }
      const customDaysInput = form.querySelector("input[name='custom_days']");
      const customPriceDisplay = form.querySelector("[data-custom-price-display]");
      const customPriceText = form.querySelector("[data-custom-price]");
      const customConfirmText = form.querySelector("[data-custom-confirm]");

      const updateCustomPrice = () => {
        if (!customDaysInput) {
          return;
        }
        const parsedDays = Number.parseInt((customDaysInput.value || "").trim(), 10);
        const isValid = Number.isFinite(parsedDays) && parsedDays > 0;
        if (!isValid) {
          customPriceDisplay && customPriceDisplay.classList.add("d-none");
          customConfirmText && customConfirmText.classList.add("d-none");
          if (customPriceText) {
            customPriceText.textContent = "₹0";
          }
          if (customConfirmText) {
            customConfirmText.textContent = "Total price is ₹0. Do you want to pay?";
          }
          return;
        }
        if (customPriceText) {
          customPriceText.textContent = `₹${parsedDays}`;
        }
        if (customConfirmText) {
          customConfirmText.textContent = `Total price is ₹${parsedDays}. Do you want to pay?`;
        }
        customPriceDisplay && customPriceDisplay.classList.remove("d-none");
        customConfirmText && customConfirmText.classList.remove("d-none");
      };

      if (customDaysInput) {
        customDaysInput.addEventListener("input", updateCustomPrice);
        updateCustomPrice();
      }

      button.addEventListener("click", async () => {
        const submitUrl = form.getAttribute("action");
        const csrf = (form.querySelector("input[name='csrf_token']") || {}).value || "";
        const planKey = (form.querySelector("input[name='plan_key']") || {}).value || "";
        let customDaysValue = null;
        if (!submitUrl || !planKey) {
          window.location.href = toStatusUrl("Plan details are missing.");
          return;
        }
        if (customDaysInput) {
          const parsedDays = Number.parseInt((customDaysInput.value || "").trim(), 10);
          if (!Number.isFinite(parsedDays) || parsedDays <= 0) {
            window.location.href = toStatusUrl("Please enter valid custom days.");
            return;
          }
          customDaysValue = parsedDays;
          const confirmed = window.confirm(`Total price is ₹${parsedDays}. Do you want to pay?`);
          if (!confirmed) {
            return;
          }
        }
        if (typeof window.Razorpay === "undefined") {
          window.location.href = toStatusUrl("Razorpay checkout failed to load.");
          return;
        }

        const originalLabel = button.textContent;
        button.disabled = true;
        button.textContent = "Opening checkout...";
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
                window.location.href = payload.status_url_on_dismiss || toStatusUrl("Payment was cancelled.");
              },
            },
          });
          checkout.open();
        } catch (error) {
          const message = (error && error.message) || "Payment could not be started.";
          window.location.href = toStatusUrl(message);
          return;
        } finally {
          button.disabled = false;
          button.textContent = originalLabel || "Activate plan";
        }
      });
    });
  };

  window.setupUploadUX && window.setupUploadUX();
})();
