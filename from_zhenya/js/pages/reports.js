const status = document.getElementById("status");

const csvInput = document.getElementById("file-csv");
const csvDrop = document.getElementById("drop-csv");

const xlsxInput = document.getElementById("file-xlsx");
const xlsxDrop = document.getElementById("drop-xlsx");

setupDropZone(csvDrop, csvInput, "/upload", "CSV");
setupDropZone(xlsxDrop, xlsxInput, "/upload-xlsx", "XLSX");

function setupDropZone(zone, input, endpoint, label) {
  zone.onclick = () => input.click();

  zone.ondragover = (e) => {
    e.preventDefault();
    zone.classList.add("dragover");
  };

  zone.ondragleave = () => zone.classList.remove("dragover");

  zone.ondrop = (e) => {
    e.preventDefault();
    zone.classList.remove("dragover");
    if (e.dataTransfer.files.length) {
      upload(e.dataTransfer.files[0], endpoint, label);
    }
  };

  input.onchange = () => {
    if (input.files.length) {
      upload(input.files[0], endpoint, label);
    }
    input.value = "";
  };
}

async function upload(file, endpoint, label) {
  status.hidden = false;
  status.textContent = `Загрузка ${label}...`;
  status.className = "status";

  const form = new FormData();
  form.append("file", file);

  try {
    const res = await fetch(endpoint, { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Ошибка");

    status.textContent = `${label}: загружено ${data.added} строк`;
    status.className = "status ok";
  } catch (err) {
    status.textContent = "Ошибка: " + err.message;
    status.className = "status err";
  }
}