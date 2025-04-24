import { initViewer, loadModel } from './viewer.js';
import { initTree } from './sidebar.js';
import { ColorPickerEditor } from './colorpicker.js';

const dbIdToIfcGUIDMap = {};


const gridOptions = {
    defaultColDef: {
        resizable: true,
    },
    columnDefs: [
        {
            headerName: 'Elementkategorie',
            field: 'kategorie',
            editable: false, // Prevent editing the name
        },
        {
            headerName: 'Farbe',
            field: 'farbe',
            editable: true,
            cellEditor: ColorPickerEditor, // Use the custom color picker editor
            cellRenderer: (params) => {
                return `<div style="background-color:${params.value}; width: 100%; height: 100%;"></div>`;
            },
        },
    ],
    domLayout: 'autoHeight',
    onGridReady: (event) => event.api.sizeColumnsToFit(),
    rowData: [],
    rowSelection: 'single',
    onCellValueChanged: handleColorChange, // Handle color changes
    onSelectionChanged: handleSelectionChanged, // Handle row selection
};
const API_URL = 'http://localhost:8001/api';
const login = document.getElementById('autodeskSigninButton');
let viewer;
let gridApi;
let projectID;
let versionID;
let accessToken

try {
    const resp = await fetch('/api/auth/profile');
    if (resp.ok) {
        const user = await resp.json();
        login.innerText = `Logout (${user.name})`;
        login.onclick = () => {
            const iframe = document.createElement('iframe');
            iframe.style.visibility = 'hidden';
            iframe.src = 'https://accounts.autodesk.com/Authentication/LogOut';
            document.body.appendChild(iframe);
            iframe.onload = () => {
                window.location.replace('/api/auth/logout');
                document.body.removeChild(iframe);
            };
        }
        viewer = await initViewer(document.getElementById('apsViewer'));
        
        const gridContainer = document.getElementById('agGrid');
        
        gridApi = new agGrid.createGrid(gridContainer, gridOptions);
        document.getElementById('excelFileInput').addEventListener('change', handleFileSelect);
        document.getElementById('save').addEventListener('click', handleSave);


        initTree('#tree', async (projectId, versionId) => {
            accessToken = await getAuthToken();
            projectID = projectId;
            versionID = versionId;

            // Generate the URN from versionId (if needed)
            const urn = window.btoa(versionId).replace(/=/g, '');
            console.log(urn);
            loadModel(viewer, urn);

            // Fetch element materials from the backend using the signed URL
            //const elements = await fetch(`${API_URL}/extract_ifc`, {
            //    method: 'POST',
            //    headers: {
            //        'Content-Type': 'application/json'
            //    },
            //    body: JSON.stringify({ versionId: versionId, projectId: projectId, accessToken: accessToken })
            //}).then(response => response.json());

            //console.log(elements)

            //if (gridApi) {
            //    gridApi.setRowData(Object.values(elements));
            //}

            viewer.addEventListener(Autodesk.Viewing.GEOMETRY_LOADED_EVENT, async () =>
            {
                const elements = await getAllElementsAndPropertiesWithColor(viewer);

                if (gridApi) {
                    gridApi.setRowData(
                        elements.map((element) => ({
                            kategorie: element.category,
                            farbe: element.color,
                            dbIds: element.dbIds, // Include the corresponding element IDs
                        }))
                    );
                }

                console.log("dbId to IFC GUID map populated:", dbIdToIfcGUIDMap);
            }); 
        });
        


    } else {
        login.innerText = 'Login';
        login.onclick = () => window.location.replace('/api/auth/login');
    }
    login.style.visibility = 'visible';
} catch (err) {
    alert('Could not initialize the application. See console for more details.');
    console.error(err);
}

jQuery.ajax(
    {
    url: '/api/clientId',
    success: function (res) {
      $('#clientId').val(res.clientId);
      $("#provisionAccountSave").click(function () {
        $('#provisionAccountModal').modal('toggle');
      });
    }
  });

  //#region Excel Reading

async function getAuthToken()
{
            const response = await fetch('/api/auth/threeleggedtoken');
            const data = await response.json();
            return data.access_token;
}

async function handleSave(event) {
    if (!projectID || !versionID) {
        console.error("Project ID or Version ID is not set.");
        alert("Cannot save because project or version information is missing.");
        return;
    }

    console.log("Saving data to backend...");

    // Collect updated data from the grid
    const rowData = [];
    gridApi.forEachNode((node) => rowData.push(node.data));

    const elementsToUpdate = rowData.map((row) => {
        const ifcGUIDs = row.dbIds.map((dbId) => dbIdToIfcGUIDMap[dbId]).filter(Boolean);
        return {
            ifcGUIDs, // Send IFC GUIDs instead of dbIds
            color: row.farbe, // Assuming `farbe` is the updated hex color
        };
    });

    // Send the data to the backend
    try {
        const response = await fetch(`${API_URL}/update_ifc`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                versionID,
                projectID,
                elements: elementsToUpdate,
                accessToken,
            }),
        });

        if (response.ok) {
            const result = await response.json();
            console.log("Save successful:", result);
            alert("Changes saved successfully.");
        } else {
            console.error("Save failed:", response.statusText);
            alert("Failed to save changes.");
        }
    } catch (err) {
        console.error("Error saving data:", err);
        alert("An error occurred while saving changes.");
    }
}


async function handleFileSelect(event) {
    const fileInput = event.target;

    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();

    reader.onload = (e) => {
        const data = new Uint8Array(e.target.result);
        const workbook = XLSX.read(data, { type: 'array' });

        // Check if the required sheet exists
        const targetSheetName = "Tabelle1";
        if (!workbook.SheetNames.includes(targetSheetName)) {
            alert(`The workbook does not contain a sheet named "${targetSheetName}".`);
            return;
        }

        const sheet = workbook.Sheets[targetSheetName];

        // Convert the target sheet to JSON for easier processing
        const rows = XLSX.utils.sheet_to_json(sheet, { header: 1 });

        // Ensure the file has the correct structure
        //if (!rows[1] || rows[1][2] !== "BIM-Elementkategorie") {
          //  alert("The file format is incorrect. Column C (row 2) must be 'BIM-Elementkategorie'.");
            //return;
        //}

        const categoryData = [];

        // Iterate through the rows to collect data
        for (let i = 0; i < rows.length; i++) {
            const categoryValue = rows[i][0]; // Column C (BIM-Elementkategorie)
            console.log(categoryValue);
            if (!categoryValue) continue;

            // Initialize a flag to track if a valid "Farbe" was found
            let foundColor = false;

            // Check the following rows for descriptions in column G
            let j = i;
            categoryData.push({
                            category: categoryValue,
                            color: rows[j][2],
            });
            foundColor = true;


            // If no valid "Farbe" is found, add a default entry (optional)
            if (!foundColor) {
                console.warn(`No "Farbe" property found for category '${categoryValue}'.`);
                categoryData.push({
                    category: categoryValue,
                    color: null, // Use `null` or a default color, if desired
                });
            }
        }

        console.log("Category Data:", categoryData);

        // Update the grid and viewer
        if (gridApi) {

            const rowData = [];
            gridApi.forEachNode((node) => rowData.push(node.data));

            categoryData.forEach(({ category, color }) => {
                if (!color || !isValidHex(color)) {
                    console.warn(`Skipping update for category '${category}' due to invalid or null color.`);
                    return;
                }
                rowData.forEach((row) => {
                    if (row.kategorie.includes(category)) {
                        console.log("finally")
                        row.farbe = color; // Update the color in the grid
                        handleColorChange({ data: row, colDef: { field: 'farbe' }, newValue: color });
                    }
                });
            });

            // Refresh the grid to reflect changes
            gridApi.setRowData(rowData);
        }
    };

    reader.onerror = (err) => {
        console.error("Error reading file:", err);
        alert("Failed to read the file.");
    };

    reader.readAsArrayBuffer(file);

    resetFileInput(fileInput);

}

function resetFileInput(inputElement) {
    inputElement.value = ''; // Clear the input's value
}

function isValidHex(hex) {
    if (typeof hex !== "string") return false;
    // Match valid hex color codes (# followed by 3 or 6 hexadecimal characters)
    return /^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$/.test(hex);
}

async function getAllElementsAndPropertiesWithColor(viewer) {
    return new Promise((resolve, reject) => {
        viewer.model.getObjectTree((tree) => {
            const allDbIds = [];
            tree.enumNodeChildren(tree.getRootId(), (dbId) => {
                allDbIds.push(dbId);
            }, true);

            const elementData = [];
            let processed = 0;

            allDbIds.forEach((dbId) => {
                viewer.getProperties(dbId, (props) => {
                    processed++;
                    const bimCategoryProperty = props.properties.find(
                        (prop) =>
                            prop.displayCategory === "HLS" &&
                            prop.displayName === "Systemabkürzung"
                    );

                    const ifcGUIDProperty = props.properties.find(
                        (prop) =>
                            prop.displayName === "IfcGUID"
                    );
                    if (ifcGUIDProperty) {
                        dbIdToIfcGUIDMap[dbId] = ifcGUIDProperty.displayValue;
                        
                    }

                    const materialProperty = props.properties.find(
                        (prop) => prop.displayCategory === "IFC Material" && prop.displayName === "Color"
                    );

                    let color = null;
                    if (materialProperty && materialProperty.displayValue) {
                        const rgbValues = materialProperty.displayValue.match(/\d+/g);
                        if (rgbValues && rgbValues.length === 3) {
                            color = `rgb(${rgbValues[0]}, ${rgbValues[1]}, ${rgbValues[2]})`;
                        }
                    }

                    if (bimCategoryProperty) {
                        elementData.push({
                            category: bimCategoryProperty.displayValue,
                            color: color || "No color defined",
                            dbIds: [dbId], // Include dbId in the data
                        });
                    }

                    if (processed === allDbIds.length) {
                        const deduplicatedData = deduplicateByCategoryAndColor(elementData);
                        resolve(deduplicatedData);
                    }
                }, reject);
            });
        }, reject);
    });
}


function deduplicateByCategoryAndColor(data) {
    const seen = new Map();
    return data.reduce((result, item) => {
        const key = `${item.category}-${item.color}`;
        if (seen.has(key)) {
            seen.get(key).dbIds.push(...item.dbIds);
        } else {
            seen.set(key, { ...item, dbIds: [...item.dbIds] });
            result.push(seen.get(key));
        }
        return result;
    }, []);
}

function handleSelectionChanged() {
    const selectedRows = gridApi.getSelectedRows();

    // Clear any previous selections
    viewer.clearSelection();

    if (selectedRows.length > 0) {
        const { dbIds } = selectedRows[0]; // Get element IDs for the selected row

        // Select elements in the viewer
        viewer.select(dbIds);
        viewer.fitToView(dbIds); // Zoom to the selected elements
    }
}

function handleColorChange(event) {
    const { data, colDef, newValue } = event;

    if (colDef.field === 'farbe') {
        const { dbIds } = data; // Retrieve the corresponding element IDs
        const color = hexToRGB(newValue); // Convert hex to RGB

        // Update viewer element colors
        dbIds.forEach((dbId) => {
            setColorForElementAndChildrenAcrossModels(viewer,dbId,new THREE.Vector4(color.r / 255, color.g / 255, color.b / 255))
            //viewer.setThemingColor(dbId, new THREE.Vector4(color.r / 255, color.g / 255, color.b / 255, 1));
        });

        console.log(`Updated color of elements ${dbIds} to ${newValue}`);
    }
}

// Utility function to convert hex to RGB
function hexToRGB(hex) {
    const bigint = parseInt(hex.replace('#', ''), 16);
    return {
        r: (bigint >> 16) & 255,
        g: (bigint >> 8) & 255,
        b: bigint & 255,
    };
}

function cleanDisplayColor(viewer) {
    viewer.clearThemingColors();
}

function setColorForElementAndChildrenAcrossModels(viewer, elementId, color) {
    if (!viewer || !viewer.getVisibleModels) {
        console.error("Viewer or models are unavailable.");
        return;
    }

    const viewerModels = viewer.getVisibleModels(); // Get all visible models

    let elementFound = false;

    for (const model of viewerModels) {
        const instanceTree = model.getInstanceTree();
        if (!instanceTree) {
            console.warn("Instance tree unavailable for a model.");
            continue;
        }

        // Check if the element exists in the current model
        if (instanceTree.getNodeName(elementId) !== null) {
            elementFound = true;

            // Apply the color to the element and its children
            setColorForElementAndChildren(viewer, elementId, color, model);
            break;
        }
    }

    if (!elementFound) {
        console.warn(`Element ID ${elementId} not found in any visible model.`);
    }
}

function setColorForElementAndChildren(viewer, elementId, color, model) {
    const instanceTree = model.getInstanceTree();
    if (!instanceTree) {
        console.error("Instance tree is unavailable.");
        return;
    }

    // Recursive function to apply color
    function applyColorRecursively(dbId) {
        // Set color for the current element
        viewer.setThemingColor(dbId, color, model);

        // Recursively set color for children
        instanceTree.enumNodeChildren(dbId, function(childId) {
            applyColorRecursively(childId);
        });
    }

    // Start recursion with the given elementId
    applyColorRecursively(elementId);
}





