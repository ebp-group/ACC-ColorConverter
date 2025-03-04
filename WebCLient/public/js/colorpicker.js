export class ColorPickerEditor {
    init(params) {
        this.eInput = document.createElement('input');
        this.eInput.type = 'color';
        this.eInput.value = params.value || '#ffffff'; // Default to white if no value
        this.eInput.style.width = '100%';

        this.onChange = () => {
            params.stopEditing(); // Stop editing when color is selected
        };
        this.eInput.addEventListener('input', this.onChange);
    }

    getGui() {
        return this.eInput;
    }

    getValue() {
        return this.eInput.value; // Return the selected color
    }

    destroy() {
        this.eInput.removeEventListener('input', this.onChange);
    }
}