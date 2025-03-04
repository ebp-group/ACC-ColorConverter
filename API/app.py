from flask import Flask, request, jsonify
from urllib.parse import urlparse, urlunparse

from flask_cors import CORS
import requests
import ifcopenshell
import ifcopenshell.guid
import os
import logging
import urllib.parse
import json
import time

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')


def download_ifc_file(project, version, access_token, local_file_path):
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/vnd.api+json'
    }

    # Encode the version ID
    encoded_version_id = urllib.parse.quote(version, safe='')

    # Get the version details
    url = f"https://developer.api.autodesk.com/data/v1/projects/{project}/versions/{encoded_version_id}"

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to get version details: {response.status_code} - {response.text}")

    version_details = response.json()

    # Extract the download URL from the version details
    try:
        storage_url = version_details['data']['relationships']['storage']['meta']['link']['href']
    except KeyError:
        raise Exception("Download URL not found in version details.")
    
    signed_url = get_signed_s3_url(access_token,storage_url)

    download_file_from_s3(signed_url,local_file_path)

    logging.info(f"File downloaded successfully to {local_file_path}")

# Step 2: Get the signed S3 URL
def get_signed_s3_url(access_token, bucket_key):

    parsed_url = urlparse(bucket_key)
    path = parsed_url.path
    if ".ifc" in path:
        base_path = path[:path.index(".ifc") + 4]  # +4 to include '.ifc'

    # Rebuild the base URL without query parameters
    bucket_key = urlunparse((parsed_url.scheme, parsed_url.netloc, base_path, '', '', ''))

    url = f"{bucket_key}/signeds3download"
    headers = {"Authorization": f"Bearer {access_token}",
                       'Content-Type': 'application/vnd.api+json'
                       }
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()["url"]

# Step 3: Download the file from the signed S3 URL
def download_file_from_s3(signed_url, local_file_path):
    response = requests.get(signed_url, stream=True)
    response.raise_for_status()
    with open(local_file_path, "wb") as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)
    print(f"File downloaded successfully to {local_file_path}")

def extract_material_info(ifc_file_path):
    ifc_model = ifcopenshell.open(ifc_file_path)
    materials_dict = {}
    
    for element in ifc_model.by_type('IfcProduct'):
        if element.HasAssociations:
            for association in element.HasAssociations:
                if association.is_a('IfcRelAssociatesMaterial'):
                    material = association.RelatingMaterial
                    materials = []
                    
                    if material.is_a('IfcMaterial'):
                        materials.append(material)
                    elif material.is_a('IfcMaterialList'):
                        materials.extend(material.Materials)
                    elif material.is_a('IfcMaterialLayerSetUsage'):
                        for layer in material.ForLayerSet.MaterialLayers:
                            materials.append(layer.Material)
                    
                    for mat in materials:
                        material_name = mat.Name
                        material_color = get_material_color(mat, ifc_model)

                        if material_color == "Unknown Color":
                            material_color = set_default_color(ifc_model, mat)

                        if material_name not in materials_dict:
                            materials_dict[material_name] = {}

                        if material_color not in materials_dict[material_name]:
                            materials_dict[material_name][material_color] = 0

                        materials_dict[material_name][material_color] += 1
    
    return materials_dict, ifc_model

def get_material_color(material, ifc_model):
    """
    Extracts the color of a material if available.
    """
    color = "Unknown Color"
    
    for definition in material.HasRepresentation:
        if definition.is_a('IfcMaterialDefinitionRepresentation'):
            for rep in definition.Representations:
                if rep.is_a('IfcStyledRepresentation'):
                    for item in rep.Items:
                        if item.is_a('IfcStyledItem'):
                            for style in item.Styles:
                                if style.is_a('IfcSurfaceStyle'):
                                    for surface_style in style.Styles:
                                        if surface_style.is_a('IfcSurfaceStyleRendering'):
                                            color = extract_rgb(surface_style)
                                            break
    return color

def extract_rgb(surface_style_rendering):
    """
    Extracts the RGB color from the IfcSurfaceStyleRendering entity.
    """
    red = surface_style_rendering.SurfaceColour.Red * 255
    green = surface_style_rendering.SurfaceColour.Green * 255
    blue = surface_style_rendering.SurfaceColour.Blue * 255
    return f"RGB({int(red)}, {int(green)}, {int(blue)})"

def get_ifc_schema_version(ifc_model):
    """
    Determines the IFC schema version.
    """
    schema_version = ifc_model.schema
    return schema_version

def set_default_color(ifc_model, material):
    """
    Sets the material color to red if it is unknown and creates a new IfcSurfaceStyleRendering.
    Adjusts logic based on IFC schema version.
    """
    red = 1.0  # Normalized value for Red
    green = 0.0  # Normalized value for Green
    blue = 0.0  # Normalized value for Blue

    # Create a new IfcColourRgb entity for red
    new_color = ifc_model.create_entity('IfcColourRgb', Name="Red", Red=red, Green=green, Blue=blue)
    
    # Create a new IfcSurfaceStyleRendering entity using the new color
    new_rendering = ifc_model.create_entity('IfcSurfaceStyleRendering', SurfaceColour=new_color)
    
    # Create a new IfcSurfaceStyle and associate the rendering to it
    new_surface_style = ifc_model.create_entity('IfcSurfaceStyle', Name="Red Surface Style", Styles=[new_rendering])

    schema_version = get_ifc_schema_version(ifc_model)

    if schema_version == "IFC4":
        if not material.HasRepresentation:
            material_def_rep = ifc_model.create_entity('IfcMaterialDefinitionRepresentation')
            material.DefinitionRepresentation = material_def_rep
            material_def_rep.Representations = []
        else:
            material_def_rep = material.HasRepresentation[0]
            # Convert to a list if it's not already
            if isinstance(material_def_rep.Representations, tuple):
                material_def_rep.Representations = list(material_def_rep.Representations)
    else:  # For IFC2x3 and earlier
        if not material.HasRepresentation:
            # Here, the Representation will be directly attached to the IfcStyledItem or other suitable entities.
            material_def_rep = None
        else:
            material_def_rep = material.HasRepresentation[0]
            if isinstance(material_def_rep.Representations, tuple):
                material_def_rep.Representations = list(material_def_rep.Representations)

    # Create a new styled item to associate with the surface style
    styled_item = ifc_model.create_entity('IfcStyledItem', Item=None, Styles=[new_surface_style])

    if schema_version == "IFC4":
        if material_def_rep is not None:
            # Associate the styled item with the material's representation
            material_def_rep.Representations = material_def_rep.Representations + (styled_item,)
    else:
        # For IFC2x3, apply the styled item directly to an appropriate representation
        # Handle association according to the schema
        material.HasRepresentation = material.HasRepresentation + (styled_item,) if material.HasRepresentation else (styled_item,)

    return "RGB(255, 0, 0)"

def save_ifc_model(ifc_model, output_file_path):
    """
    Saves the modified IFC model to a new file.
    """
    ifc_model.write(output_file_path)

def create_owner_history(ifc_file):
    # Create IfcPerson and IfcOrganization
    person = ifc_file.create_entity("IfcPerson", FamilyName="User", GivenName="IFC")
    organization = ifc_file.create_entity("IfcOrganization", Name="MyOrganization")

    # Create IfcPersonAndOrganization
    person_and_org = ifc_file.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=organization)

    # Create IfcApplication
    application = ifc_file.create_entity("IfcApplication", ApplicationDeveloper=organization, Version="1.0", ApplicationFullName="IFC App", ApplicationIdentifier="IFC_APP")

    # Create IfcOwnerHistory
    owner_history = ifc_file.create_entity("IfcOwnerHistory", OwningUser=person_and_org, OwningApplication=application, State=None, ChangeAction="ADDED", LastModifiedDate=None, LastModifyingUser=None, LastModifyingApplication=None, CreationDate=int(time.time()))

    return owner_history

def update_element_and_children_colors(ifc_file, root_element, rgb):
    # Step 1: Create the surface style and presentation style assignment
    surface_style = ifc_file.create_entity(
        "IfcSurfaceStyle",
        Name="ElementColor",
        Side="BOTH",
        Styles=[
            ifc_file.create_entity(
                "IfcSurfaceStyleRendering",
                SurfaceColour=ifc_file.create_entity(
                    "IfcColourRgb", Name=None, Red=rgb[0], Green=rgb[1], Blue=rgb[2]
                ),
                Transparency=0.0,
                ReflectanceMethod="MATT",
            )
        ],
    )

    presentation_style = ifc_file.create_entity(
        "IfcPresentationStyleAssignment", Styles=[surface_style]
    )

    # Step 2: Assign the presentation style to IfcMappedItem
    def apply_style_to_mapped_item(mapped_item):
        """Assign a presentation style to an IfcMappedItem."""
        # Check if the mapped item already has an IfcStyledItem
        styled_item = None
        for style in ifc_file.by_type("IfcStyledItem"):
            if style.Item == mapped_item:
                styled_item = style
                break

        if not styled_item:
            # Create a new IfcStyledItem if none exists
            styled_item = ifc_file.create_entity(
                "IfcStyledItem", Item=mapped_item, Styles=()
            )

        # Ensure the presentation style is assigned to the styled item
        styled_item.Styles = (presentation_style,)

    # Step 3: Iterative traversal
    def traverse_and_apply_style(root):
        visited = set()
        stack = [root]

        while stack:
            element = stack.pop()

            # Skip already visited elements
            if element.id() in visited:
                continue
            visited.add(element.id())

            # Apply style to IfcMappedItem in the representation
            if hasattr(element, "Representation") and element.Representation:
                for shape_representation in element.Representation.Representations:
                    for item in shape_representation.Items:
                        if item.is_a("IfcMappedItem"):
                            apply_style_to_mapped_item(item)

            # Add children via decomposition
            if hasattr(element, "IsDecomposedBy"):
                for decomposition in element.IsDecomposedBy:
                    stack.extend(decomposition.RelatedObjects)

            # Add related objects defined by type
            if hasattr(element, "IsDefinedBy"):
                for definition in element.IsDefinedBy:
                    if definition.is_a("IfcRelDefinesByType"):
                        stack.extend(definition.RelatedObjects)

    # Start traversal
    traverse_and_apply_style(root_element)

    print("Finished assigning colors to IfcMappedItem.")



def update_element_and_children_colors3(ifc_file, root_element, rgb):
    # Step 1: Create the surface style and presentation style assignment
    surface_style = ifc_file.create_entity(
        "IfcSurfaceStyle",
        Name="ElementColor",
        Side="BOTH",
        Styles=[
            ifc_file.create_entity(
                "IfcSurfaceStyleRendering",
                SurfaceColour=ifc_file.create_entity(
                    "IfcColourRgb", Name=None, Red=rgb[0], Green=rgb[1], Blue=rgb[2]
                ),
                Transparency=0.0,
                ReflectanceMethod="MATT",
            )
        ],
    )

    presentation_style = ifc_file.create_entity(
        "IfcPresentationStyleAssignment", Styles=[surface_style]
    )

    # Step 2: Assign the presentation style to IfcMappedItem
    def apply_style_to_mapped_item(mapped_item):
        """Assign a presentation style to an IfcMappedItem."""
        styled_item = None
        for style in ifc_file.by_type("IfcStyledItem"):
            if style.Item == mapped_item:
                styled_item = style
                break

        if not styled_item:
            # Create a new IfcStyledItem if none exists
            styled_item = ifc_file.create_entity(
                "IfcStyledItem", Item=mapped_item, Styles=()
            )

        # Assign the presentation style to the styled item
        styled_item.Styles = (presentation_style,)

    def traverse_and_apply_style(element):
        """Traverse elements and apply styles to IfcMappedItem."""
        if not hasattr(element, "Representation") or not element.Representation:
            return

        for shape_representation in element.Representation.Representations:
            for item in shape_representation.Items:
                if item.is_a("IfcMappedItem"):
                    # Apply style to IfcMappedItem
                    apply_style_to_mapped_item(item)

        # Process related objects defined by type
        if hasattr(element, "IsDefinedBy"):
            for definition in element.IsDefinedBy:
                if definition.is_a("IfcRelDefinesByType"):
                    for related_object in definition.RelatedObjects:
                        traverse_and_apply_style(related_object)

    # Start traversal
    traverse_and_apply_style(root_element)


def update_element_and_children_colors2(ifc_file, root_element, rgb):

    

    # Step 1: Create a surface style with the specified color
    surface_style = ifc_file.create_entity(
        "IfcSurfaceStyle",
        Name="ElementColor",
        Side="BOTH",
        Styles=[
            ifc_file.create_entity(
                "IfcSurfaceStyleRendering",
                SurfaceColour=ifc_file.create_entity(
                    "IfcColourRgb", Name="Test", Red=rgb[0], Green=rgb[1], Blue=rgb[2]
                ),
                Transparency=0.0,
                ReflectanceMethod="MATT",
            )
        ],
    )

    # Step 2: Assign the color directly to elements
    def apply_style_to_shape_representation(element):
        if not hasattr(element, "Representation") or not element.Representation:
            return

        for shape_representation in element.Representation.Representations:
            for item in shape_representation.Items:
                if item.is_a("IfcRepresentationMap"):
                    # Handle IfcRepresentationMap
                    mapped_representation = item.MappingSource.MappedRepresentation
                    for mapped_item in mapped_representation.Items:
                        assign_style_to_item(mapped_item)
                elif item.is_a("IfcMappedItem"):
                    # Handle IfcMappedItem
                    source_representation = item.MappingSource.MappedRepresentation
                    for mapped_item in source_representation.Items:
                        assign_style_to_item(mapped_item)
                else:
                    # Handle regular representation items
                    assign_style_to_item(item)

    def assign_style_to_item(item):
        """Assign a surface style to a representation item."""
        # Check if there's already an IfcStyledItem
        styled_item = None
        for style in ifc_file.by_type("IfcStyledItem"):
            if style.Item == item:
                styled_item = style
                break

        if not styled_item:
            # Create a new IfcStyledItem if none exists
            styled_item = ifc_file.create_entity("IfcStyledItem", Item=item, Styles=())

        # Assign the surface style to the styled item
        styled_item.Styles = (surface_style,)

    # Step 3: Recursively apply styles to the element and its children
    def traverse_and_apply_style(element):
        apply_style_to_shape_representation(element)

        # Process related objects defined by type
        if hasattr(element, "IsDefinedBy"):
            for definition in element.IsDefinedBy:
                if definition.is_a("IfcRelDefinesByType"):
                    for related_object in definition.RelatedObjects:
                        traverse_and_apply_style(related_object)
    # Start traversal and apply styles
    try:
        traverse_and_apply_style(root_element)
    except:
        here = "Test"

def get_children_of_element(ifc_file, element):
    """Get the children of an element."""
    children = []
    for rel in element.HasAssociations:
        if isinstance(rel.RelatedObjects, list):
            children.extend(rel.RelatedObjects)
    return children

def assign_color_to_material(ifc_file, material, rgb):
    """Assign a color to a material using IfcSurfaceStyle for IFC2X3 schema."""
    # Step 1: Create an IfcColourRgb entity for the color
    color_rgb = ifc_file.create_entity(
        "IfcColourRgb",
        Name="CustomColor",
        Red=rgb[0],
        Green=rgb[1],
        Blue=rgb[2]
    )

    # Step 2: Create an IfcSurfaceStyleShading entity
    surface_style_shading = ifc_file.create_entity(
        "IfcSurfaceStyleShading",
        SurfaceColour=color_rgb
    )

    # Step 3: Create an IfcSurfaceStyle entity and assign shading
    surface_style = ifc_file.create_entity(
        "IfcSurfaceStyle",
        Side="BOTH",  # Apply to both sides
        Styles=[surface_style_shading]  # Add the shading style
    )

    # Step 4: Create an IfcStyledItem to link to geometry (if applicable)
    styled_item = ifc_file.create_entity(
        "IfcStyledItem",
        Item=None,  # Geometric item this style applies to
        Styles=[surface_style]
    )

    # Step 5: Link to material through IfcMaterialDefinitionRepresentation
    material_def_rep = ifc_file.create_entity(
        "IfcMaterialDefinitionRepresentation",
        RepresentedMaterial=material,
        Representations=[styled_item]
    )

def get_geometric_representation_item(ifc_file, material):
    """Retrieve or create a geometric representation item for the material."""
    # Placeholder: This should retrieve or create the geometry for the material
    # In real cases, it depends on how geometry is represented in your IFC file
    return material  # Replace with actual logic

def get_representation_context(ifc_file):
    """Retrieve or create the representation context for styled representations."""
    # Placeholder: This should retrieve or create a suitable representation context
    # In real cases, it depends on the IFC file setup
    return ifc_file.by_type("IfcGeometricRepresentationContext")[0]  # Replace with actual logic



# Utility function to validate if the color is a valid hex color code
def isValidHex(hex):
    if isinstance(hex, str) and len(hex) in [4, 7] and hex[0] == "#":
        try:
            int(hex[1:], 16)  # Try to parse the string to an integer
            return True
        except ValueError:
            return False
    return False


def get_item_id_from_version(project_id, version_id, access_token):
    """
    Retrieves the item_id associated with a version_id from Autodesk APS Data Management API.
    
    :param project_id: Autodesk Project ID
    :param version_id: The version ID for which we need the item_id
    :param access_token: OAuth token for authentication
    :return: item_id (str) or None if not found
    """
    encoded_version_id = urllib.parse.quote(version_id, safe='')

    url = f"https://developer.api.autodesk.com/data/v1/projects/{project_id}/versions/{encoded_version_id}"
    headers = {"Authorization": f"Bearer {access_token}"}

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json()
        item_id = data["data"]["relationships"]["item"]["data"]["id"]
        return item_id
    else:
        print(f"Error: {response.status_code} - {response.text}")
        return None

def get_folder_id_from_item(project_id, item_id, access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    # 2️⃣ Step 2: Get folder_id from item_id
    item_url = f"https://developer.api.autodesk.com/data/v1/projects/{project_id}/items/{item_id}"
    item_response = requests.get(item_url, headers=headers)

    if item_response.status_code != 200:
        print(f"Error fetching item details: {item_response.status_code} - {item_response.text}")
        return None

    item_data = item_response.json()
    folder_id = item_data["data"]["relationships"]["parent"]["data"]["id"]

    return folder_id

def upload_to_cloud(project_id, version_id, file_path, file_name, access_token):
    """
    Uploads a new version of a file to Autodesk's BIM 360 cloud.

    :param project_id: The ID of the project.
    :param item_id: The lineage ID of the item to update.
    :param file_path: The local path to the file to upload.
    :param file_name: The name of the file to upload.
    :param access_token: The OAuth access token.
    :return: The response of the version creation request.
    """
    item_id = get_item_id_from_version(project_id, version_id, access_token)
    folder_id = get_folder_id_from_item(project_id,item_id,access_token)
    # Step 1: Create storage
    storage_url = f"https://developer.api.autodesk.com/data/v1/projects/{project_id}/storage"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/vnd.api+json"
    }
    storage_payload = {
        "jsonapi": {"version": "1.0"},
        "data": {
            "type": "objects",
            "attributes": {"name": file_name},
            "relationships": {  # ✅ Important! Link storage to folder
                "target": {
                    "data": {"type": "folders", "id": folder_id}
                }
            }
        }
    }

    storage_response = requests.post(storage_url, headers=headers, json=storage_payload)
    if storage_response.status_code != 201:
        raise Exception(f"Failed to create storage: {storage_response.json()}")

    storage_data = storage_response.json()
    storage_id = storage_data["data"]["id"]
    signed_upload_url = storage_data["data"]["links"]["upload"]

    logging.info(f"Signed S3 URL obtained: {signed_upload_url}")

    # 2️⃣ STEP 2: UPLOAD FILE TO S3 SIGNED URL
    with open(file_path, "rb") as file_data:
        upload_response = requests.put(signed_upload_url, data=file_data, headers={"Content-Type": "application/octet-stream"})
        
        if upload_response.status_code not in [200, 201]:
            raise Exception(f"Failed to upload IFC file: {upload_response.text}")

    logging.info("File uploaded successfully to S3.")

    # 3️⃣ STEP 3: CREATE A NEW ITEM IF NO `item_id` IS PROVIDED
    if not item_id:
        item_url = f"https://developer.api.autodesk.com/data/v1/projects/{project_id}/items"
        
        item_payload = {
            "jsonapi": {"version": "1.0"},
            "data": {
                "type": "items",
                "attributes": {"name": file_name},
                "relationships": {
                    "tip": {"data": {"type": "versions", "id": storage_id}},
                    "parent": {"data": {"type": "folders", "id": folder_id}}
                }
            }
        }

        item_response = requests.post(item_url, headers=headers, json=item_payload)
        
        if item_response.status_code != 201:
            raise Exception(f"Failed to create new item: {item_response.json()}")

        item_data = item_response.json()
        item_id = item_data["data"]["id"]

        logging.info(f"New item created with ID: {item_id}")

    # 4️⃣ STEP 4: CREATE A NEW VERSION OF THE FILE
    version_url = f"https://developer.api.autodesk.com/data/v1/projects/{project_id}/versions"
    
    version_payload = {
        "jsonapi": {"version": "1.0"},
        "data": {
            "type": "versions",
            "attributes": {
                "name": file_name,
                "extension": {
                    "type": "versions:autodesk.bim360:File",
                    "version": "2.0"
                }
            },
            "relationships": {
                "item": {"data": {"type": "items", "id": item_id}},
                "storage": {"data": {"type": "objects", "id": storage_id}}
            }
        }
    }

    version_response = requests.post(version_url, headers=headers, json=version_payload)
    
    if version_response.status_code != 201:
        raise Exception(f"Failed to create new version: {version_response.json()}")

    logging.info("New version created successfully.")
    
    return version_response.json()

@app.route('/api/update_ifc', methods=['POST'])
def extract_ifc():
    data = request.json
    version = data.get('versionID')
    project = data.get('projectID')
    accessToken = data.get('accessToken')
    
    if not version:
        return jsonify({'error': 'versionId is required'}), 400

    if not project:
        return jsonify({'error': 'projectId is required'}), 400

    if not accessToken:
        return jsonify({'error': 'accessToken is required'}), 400

    # Get the current working directory
    current_directory = os.getcwd()
    
    # Construct the path to the "Temp" folder and the target file
    temp_directory = os.path.join(current_directory, 'Temp')
    local_file_path = os.path.join(temp_directory, "temp.ifc")

    # Ensure the directory exists
    if not os.path.exists(temp_directory):
        os.makedirs(temp_directory)
    
    try:
        download_ifc_file(project, version, accessToken, local_file_path)

        ifc_file = ifcopenshell.open(local_file_path)
            # Step 1: Delete all existing styles in the file
        for styled_item in ifc_file.by_type("IfcStyledItem"):
            ifc_file.remove(styled_item)
        for style_assignment in ifc_file.by_type("IfcPresentationStyleAssignment"):
            ifc_file.remove(style_assignment)
        # Parse incoming data
        color_data = request.json  # [{'id': [1, 2, 3], 'color': '#FF5733'}, ...]

        for data in color_data["elements"]:
            element_ids = data['ifcGUIDs']
            hex_color = data['color']
                # Skip if color is undefined, null, or invalid
            if not hex_color or not isValidHex(hex_color):
                continue  # Skip this iteration and move to the next one

            # Convert hex to RGB (normalized to 0-1 for IFC)
            rgb = tuple(int(hex_color.lstrip("#")[i:i+2], 16) / 255 for i in (0, 2, 4))

            for element_id in element_ids:
                element = ifc_file.by_id(element_id)
                if element:
                    update_element_and_children_colors(ifc_file, element, rgb)

        # Save updated IFC file
        ifc_file.write(local_file_path)

        # Step 5: Upload the updated IFC file to the cloud
        #upload_to_cloud(project,version, local_file_path,"1.ifc", accessToken)

        return jsonify({"status": "success", "message": "IFC file updated successfully."})
    
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        return jsonify({'error': str(e)}), 500
    #finally:
        # Optionally clean up temporary files
     #   if os.path.exists(local_file_path):
      #      os.remove(local_file_path)

if __name__ == '__main__':
    port = 8001
    print(f"Starting server on port {port}")
    app.run(port=port)


