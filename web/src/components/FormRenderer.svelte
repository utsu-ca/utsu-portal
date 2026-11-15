<script>
  import { visible } from "../lib/forms.js";

  // Renders a form from its static JSON definition. `values` is edited in place; `errors` maps a field id to its message.
  let { schema, values = $bindable({}), errors = {} } = $props();

  const toggle = (id, options, option, checked) => {
    const current = values[id] || [];
    values[id] = options.filter(o => (o === option ? checked : current.includes(o)));
  };
</script>

{#each schema.fields as f (f.id)}
  {#if visible(f, values)}
    <div>
      {#if f.type === "info"}
        <p>{f.label}</p>
      {:else if f.type === "radio" || f.type === "multiselect"}
        <fieldset>
          <legend>{f.label}{f.required ? " *" : ""}{#if f.help}<span class="help">{f.help}</span>{/if}</legend>
          {#each f.options as option}
            <label class="opt">
              {#if f.type === "radio"}
                <input type="radio" name={f.id} checked={values[f.id] === option} onchange={() => (values[f.id] = option)} />
              {:else}
                <input type="checkbox" checked={(values[f.id] || []).includes(option)}
                  onchange={e => toggle(f.id, f.options, option, e.currentTarget.checked)} />
              {/if}
              {" " + option}
            </label>
          {/each}
          <div class="err">{errors[f.id] || ""}</div>
        </fieldset>
      {:else}
        <label for={"field-" + f.id}>{f.label}{f.required ? " *" : ""}{#if f.help}<span class="help">{f.help}</span>{/if}</label>
        {#if f.type === "textarea"}
          <textarea id={"field-" + f.id} rows="5" bind:value={values[f.id]}></textarea>
        {:else if f.type === "select"}
          <select id={"field-" + f.id} bind:value={values[f.id]}>
            <option value="">—</option>
            {#each f.options as option}<option value={option}>{option}</option>{/each}
          </select>
        {:else if f.type === "checkbox"}
          <input id={"field-" + f.id} type="checkbox" bind:checked={values[f.id]} />
        {:else if f.type === "number"}
          <input id={"field-" + f.id} type="number" bind:value={values[f.id]} />
        {:else if f.type === "date"}
          <input id={"field-" + f.id} type="date" bind:value={values[f.id]} />
        {:else if f.type === "email"}
          <input id={"field-" + f.id} type="email" bind:value={values[f.id]} />
        {:else}
          <input id={"field-" + f.id} type="text" bind:value={values[f.id]} />
        {/if}
        <div class="err">{errors[f.id] || ""}</div>
      {/if}
    </div>
  {/if}
{/each}
